# WC Analytics 改善要求文档

> **版本**: v1.0  
> **日期**: 2026-05-15  
> **目标**: 2026年6月世界杯开赛前达到生产可用水平  

---

## 目录

1. [当前状态快照](#1-当前状态快照)
2. [预测机制详解](#2-预测机制详解)
3. [数据显示不准问题诊断](#3-数据显示不准问题诊断)
4. [数据抓取与清洗现状](#4-数据抓取与清洗现状)
5. [致命问题与解决方案](#5-致命问题与解决方案)
6. [项目必须达到的水平](#6-项目必须达到的水平)
7. [分阶段改善路线](#7-分阶段改善路线)
8. [验收标准](#8-验收标准)

---

## 1. 当前状态快照

### 1.1 已完成的

| 模块 | 完成度 | 说明 |
|------|--------|------|
| Elo + 泊松融合预测引擎 | ✅ 完成 | 10 子模型 + 线性融合，1951 行代码 |
| BetNN 神经网络 | ✅ 完成 | 3 层 MLP，专门检测平局信号，每天自动训练 |
| 竞彩期号同步 | ✅ 完成 | sporttery.cn API，每 30 分钟自动同步 |
| 多源赔率采集 | ✅ 完成 | zgzcw(37家) + 500.com(20+) + 竞彩官方 + BetExplorer |
| 5 玩法全覆盖 | ✅ 完成 | SPF / RQ / Score / Goals / Half |
| 策略管线 | ✅ 完成 | 校准→边际→过滤→仓位→风控，4 档风险 |
| 卡密系统 | ✅ 完成 | 生成/兑换/激活 |
| 前端看板 | ✅ 完成 | 竞彩卡片 + EV 展示 + 赔率对比 |
| 数据清洗模块 | ✅ 完成 | 6 类问题自动检测 + 修复 |
| 37 个调度任务 | ✅ 完成 | APScheduler 覆盖全流程 |
| 历史数据导入 | ✅ 完成 | 5330 场五大联赛（含赔率+结果） |
| 模型验证看板 | ✅ 完成 | 校准曲线 / 按玩法验证 / Brier Score |

### 1.2 未完成的（阻塞上线）

| 问题 | 严重程度 | 影响 | 当前状态 |
|------|---------|------|---------|
| ~~策略 API 后端无鉴权~~ | ~~🔴 致命~~ | ~~付费墙形同虚设~~ | ✅ 已修复：后端 403 拒绝 + paid_until 过期检测 |
| ~~7 个 POST 端点无认证~~ | ~~🔴 致命~~ | ~~任何人可启动/停止实时赔率~~ | ✅ 已修复：所有敏感端点已加 admin key / JWT |
| 模型淘汰赛准确率 31.2% | 🔴 致命 | 比随机还差，上线即信誉破产 | ❌ 未修复 |
| 赔率覆盖率仅 30% | 🔴 致命 | 70% 比赛预测质量断崖下降 | 🔄 进行中：cloakbrowser 已接入，OddsHarvester 桥接已搭建 |
| 测试覆盖 < 5% | 🔴 致命 | 任何改动都可能无声引入 bug | ❌ 未修复 |
| Walk-forward 泛化失败 | 🟡 严重 | 不同届世界杯间准确率跌至 51.6% | ❌ 未修复 |
| 预测快照锁定未实现 | 🟡 严重 | 无法证明"赛前预测未被篡改" | ❌ 未修复 |
| 无数据库迁移工具 | 🟡 严重 | 改表结构需手工 SQL | ❌ 未修复 |
| HTTPS 未强制 | 🟡 严重 | 数据明文传输 | ✅ 已支持：ENFORCE_HTTPS=1 环境变量 |
| ~~前端无分页~~ | ~~🟡 中等~~ | ~~31073 场数据一次性加载~~ | ✅ 后端已实现 limit/offset 分页 |
| .env 中真实 Key 存文件 | 🟡 中等 | 服务器入侵即泄露 | ⏸️ 暂不处理（用户决策） |
| ~~合规措辞风险~~ | ~~🟡 中等~~ | ~~"推荐"等措辞可能触法~~ | ✅ 已修复：25处"推荐"→"模型估算"、"建议"→"参考方向" |

---

## 2. 预测机制详解

### 2.1 胜率是怎么算出来的

项目使用**两层预测架构**，不是单一模型：

#### 主引擎（EnsembleFusion）：线性加权融合

```
最终胜率 = 0.64 × 市场赔率隐含概率
        + 0.19 × 球员状态修正
        + 0.13 × 泊松攻防模型
        + 0.04 × Elo 实力模型
```

**这四个分数各自怎么来：**

**A. 市场赔率隐含概率（权重 0.64，最强信号）**
```
P_market = normalize(1/odds_home, 1/odds_draw, 1/odds_away)
```
- 来源：竞彩官方、zgzcw 百家欧赔、500.com、BetExplorer
- 逻辑：博彩市场汇集了全球信息，赔率是经过真金白银博弈后的"群体智慧"
- **致命问题**：当没有赔率时，这个权重必须降为 0，模型退化为 Poisson+Elo

**B. 泊松攻防模型（权重 0.13）**
```
λ_home = home_attack × away_defense × form_factor × 13步修正因子
λ_away = away_attack × home_defense × form_factor × 13步修正因子

P(主队进i球, 客队进j球) = DixonColes修正 × Poisson(i|λ_home) × Poisson(j|λ_away)
```
- 13 步修正包括：xG/xGA 优先、本届赛事动态加权、中立场优势、近期状态、主客场、赛程密度、天气场地、战术相克、教练能力、伤病停赛、淘汰赛降λ、小组赛第三轮轮换
- 比分矩阵 9×9（0-8 球），归一化后从比分加总得到胜平负概率
- Dixon-Coles 修正解决独立泊松低估 0-0/1-1 的问题

**C. 球员状态修正（权重 0.19）**
```
修正系数 = f(核心球员可用率, 疲劳指数)
可用率 = key_players_available / key_players_total
疲劳惩罚 = squad_fatigue_index × 10%
```
- **致命问题**：当前 602 条球员记录中，key_injuries 和 fatigue_index 多为默认值，修正系数几乎恒为 1.0

**D. Elo 实力模型（权重 0.04）**
```
P(home) = 1 / (1 + 10^((elo_away - elo_home + neutral_advantage) / 400))
```
- 48 支世界杯球队有手动录入的 Elo 值（基于 FIFA 排名校准）
- 俱乐部球队有内置 CLUB_ELO_RATINGS（约 120 支英超/西甲/德甲/意甲/法甲球队）
- **致命问题**：Elo 权重仅 0.04，说明在历史数据上 Elo 和实际结果的关联极弱

#### 辅引擎（BetNN）：神经网络平局检测

```
输入(20维) → FC(64) → ReLU → FC(32) → ReLU → FC(16) → ReLU → FC(3) → Sigmoid
```
- 输入特征：主引擎 SPF 概率(3) + RQ 概率(3) + 比分 top3 概率(3) + 赔率(3) + Elo 差(1) + 赔率变动(3) + 联赛 one-hot(4)
- 用途：专门检测融合引擎容易漏掉的平局（因为融合引擎整体倾向主胜/客胜）
- 训练：比赛结果录入后增量训练，最小 50 样本，早停 patience=5

### 2.2 为什么数据显示不准

问题出在**数据链路中的四个环节**：

#### 环节 1：输入数据质量差
```
问题：球员数据全是默认值
影响：PlayerAdjustmentModel 修正系数 ≈ 1.0，相当于没起作用

问题：70% 比赛没有赔率
影响：market 权重降为 0，退化为 Elo(0.30) + Poisson(0.52) + Players(0.18)
      但这三个模型的原始数据质量参差不齐

问题：Elo 值依赖手动更新
影响：世界杯前 48 队 Elo 是 2026 年初快照，友谊赛结果未更新
```

#### 环节 2：权重学习过拟合
```
用 2018 世界杯 64 场学出的权重 → 测 2022 世界杯 64 场
准确率：51.6%（随机=33%）
淘汰赛：31.2%（比随机 33% 还差）
```
**结论：128 场样本不足以学到泛化的融合权重。** 这就是为什么后续导入 5330 场联赛数据重新学习，权重从 `elo=0.42` 骤降至 `elo=0.04`。但这批数据是俱乐部联赛，和世界杯场景分布不同。

#### 环节 3：融合层在赔率缺失时退化严重
```
有赔率时（30% 比赛）：
  market=0.64 → 预测 ≈ 博彩市场共识 → 相对可靠

无赔率时（70% 比赛）：
  market=0 → 依赖 Elo(0.04→0.29) + Poisson(0.13→0.48) + Players(0.19→0.23)
  但 Elo 过时、Players 默认值 → 预测质量断崖
```

#### 环节 4：前端展示逻辑有延迟
```
问题：前端 isLocked() 只检查 user.is_paid，未检查具体比赛的 license
影响：付费用户可能看到错误的状态

问题：预测数据未标注"数据新鲜度"
影响：用户不知道看到的是 30 分钟前的预测还是 3 天前的预测

问题：EV 计算使用 odds_home 字段
影响：如果赔率已更新但预测未重算，EV 值与实际脱节
```

---

## 3. 数据抓取与清洗现状

### 3.1 数据采集：框架完整，覆盖率不足

#### 已接入的数据源

| 数据源 | 类型 | 覆盖范围 | 频率 | 状态 |
|--------|------|---------|------|------|
| sporttery.cn | 竞彩官方 API | 当前在售期号 + 赔率 | 每 30min | ✅ 稳定（主力数据源） |
| zgzcw.com | 中国足彩网爬虫 | 37 家博彩公司欧赔 | 每 30min | ✅ 稳定 |
| 500.com | 五百万爬虫 | 20+ 家博彩公司欧赔 | 每 2h | ✅ 稳定 |
| BetExplorer | Web 爬虫 | 历史赔率 | 按需 | ✅ cloakbrowser JS 渲染 |
| football-data.co.uk | CSV 下载 | 五大联赛历史 5330 场 | 一次性已导入 | ✅ 完成 |
| FBref (via soccerdata) | Python 库 | xG/xGA/球员数据 | 每天 05:00 | ⚠️ 覆盖率低 |
| Odds API | 付费 API | 实时赔率 | 待配置 | ❌ Key 未配置 |
| OddsPortal (via cloakbrowser) | Playwright stealth 爬虫 | 全球 100+ 联赛赔率 | 按需 | ✅ 桥接已搭建 + cloakbrowser |
| 澳门彩票 | cloakbrowser 爬虫 | 让球盘 + 大小球 | 按需 | 🔄 框架已就绪，待确认页面结构 |
| 香港马会 | cloakbrowser 爬虫 | 足球赔率 | 按需 | 🔄 框架已就绪，待确认页面结构 |

#### 数据覆盖率实际情况

```
总比赛数：  31,073 场
即将进行：  420 场
有赔率的：  126 场 (30%)
无赔率的：  294 场 (70%)

赔率来源分布：
  zgzcw:          ~60 场（主要来源）
  500.com:        ~40 场
  football-data:  ~20 场
  jingcai:        ~6 场（当前在售期号）
  synthetic:      其余（合成赔率，不可靠）
```

#### 球员数据实际情况

```
PlayerStats 表：602 条记录
key_players_available:  多为默认值 11
key_players_total:      多为默认值 11
squad_fatigue_index:    多为默认值 0.5
key_injuries:           多为空
```

**结论：球员状态数据几乎为零，PlayerAdjustmentModel 形同虚设。**

### 3.2 数据清洗：机制完整但自动化不足

`data_cleaner.py` (687 行) 实现了 6 类清洗：

| 清洗项 | 功能 | 自动运行 |
|--------|------|---------|
| 时区统一 | naive UTC → aware UTC | ✅ 调度器 |
| 赔率去重 | 同 match_id/source/5min 窗口只保留 1 条 | ✅ 调度器 |
| 队名规范化 | TEAM_ALIASES 中粤语/简写 → 规范名 | ✅ 调度器 |
| match_code 受控 | 只允许 JC/INT/OF/FR/WC 前缀 | ✅ 调度器 |
| 零赔率修复 | 0.0 赔率 → NULL | ✅ 调度器 |
| Enum 一致性 | raw string → enum value | ✅ 调度器 |

**但清洗只修复格式问题，不解决数据缺失问题。** 294 场无赔率的比赛，清洗无法补救。

### 3.3 关键缺口

| 缺口 | 为什么重要 | 怎么补 | 进展 |
|------|-----------|--------|------|
| 实时赔率覆盖率 30% | 模型 64% 权重依赖赔率 | 接入 Odds API + cloakbrowser 渲染 | ✅ cloakbrowser 桥接已搭建 |
| 球员伤病数据为零 | Player 权重 19% 白费 | api-football.com injuries endpoint | ❌ 待执行 |
| xG 数据靠回归估算 | 回归 R²=0.86 有误差 | FBref 直接爬取 + 定期更新 | ❌ 待执行 |
| 世界杯球队 Elo 过时 | Elo 权重虽低但仍然影响 | ClubElo.com 自动同步 | ❌ 待执行 |
| 无阵容/首发数据 | 无法判断核心缺阵影响 | FBref match report 解析 | ❌ 待执行 |

---

## 4. 致命问题与解决方案

### 🔴 致命-1：付费墙仅前端实现 ✅ 已修复

**原现象**：前端 `isLocked()` 纯客户端判断，F12 可绕过。

**已实现的修复**（2026-05-15）：
1. `/api/matches/{id}/strategy` 端点已实现后端鉴权：
   - 使用 `get_optional_user` 获取用户（允许未登录访问赛后数据）
   - 未付费用户返回 `403 Forbidden`
   - `paid_until` 过期检测在服务端执行，过期自动设 `is_paid=False`
   - 赛后 `status=finished` 自动开放
2. 前端 `isLocked()` 保留为 UI 预判（非安全依赖）
3. 所有敏感 POST 端点已加 `_verify_admin_key` 或 `get_current_active_user`

**验收**：`curl /api/matches/1/strategy`（无 token）→ 403

---

### 🔴 致命-2：7 个 POST 端点无认证 ✅ 已修复

**原现象**：7 个 POST 端点任何人可调用。

**已实现的修复**（2026-05-15）：
- `/api/live-odds/start|stop` → `_verify_admin_key`
- `/api/live-hedge/position` → `_verify_admin_key`
- `/api/bet-nn/train` → `_verify_admin_key`
- `/api/sub-models/train/*` → `_verify_admin_key`
- 所有 jingcai 写操作 → `_verify_admin_key`
- sporttery 同步 → `get_current_active_user`
- 策略优化/监控 → `get_current_active_user`

**验收**：`curl -X POST /api/live-odds/start`（无 Key）→ 403

---

### 🔴 致命-3：淘汰赛准确率 31.2%

**现象**：
```
Walk-forward: 2018 训练 → 2022 测试
  小组赛: 58.3%
  淘汰赛: 31.2%  ← 比抛硬币 (33%) 还差
```

**为什么致命**：淘汰赛是世界杯关注度最高的阶段。预测准确率低于随机，上线后口碑灾难。

**根因分析**：
1. **训练样本不足**：128 场世界杯 + 5330 场俱乐部联赛，但俱乐部比赛和世界杯淘汰赛完全是不同分布
2. **淘汰赛参数硬编码**：`stage_factor = {"R16": 0.88, "QF": 0.85, ...}` 是手动调的，不是从数据学的
3. **缺少点球数据**：淘汰赛有加时/点球，当前模型只预测常规时间结果
4. **缺少淘汰赛专属特征**：战意、保守战术、定位球占比升高等因子未建模

**解决方案**：
```
Step 1 (紧急): 淘汰赛使用独立权重
  - 从近 5 届世界杯淘汰赛 (80 场) + 欧冠淘汰赛 (200+ 场) 学习专属权重
  - 预期提升：31.2% → 45%+

Step 2 (世界杯前): 增加淘汰赛专属特征
  - 点球历史数据（国家队近 5 年点球命中率）
  - 门将扑点率
  - 加时赛体能衰减模型
  - 定位球进球占比（淘汰赛定位球占比常 > 30%）

Step 3 (持续): 小组赛阶段积累 2026 数据后动态更新
```

**预计工时**：Step 1: 8 小时，Step 2: 16 小时

---

### 🔴 致命-4：赔率覆盖率仅 30%

**现象**：
```
即将进行的 420 场比赛：
  有赔率: 126 (30%)
  无赔率: 294 (70%)
```

**为什么致命**：融合权重 market=0.64，70% 比赛缺失最强信号，预测质量断崖下降。

**解决方案**：
```
1. 启用 OddsHarvester — oddsportal.com 历史 + 实时赔率
   - 安装: pip install oddsharvester (已列在 requirements.txt 注释中)
   - 覆盖: 全球 100+ 联赛，免费额度足够

2. 配置 Odds API Key
   - 注册 the-odds-api.com (免费 500 req/月)
   - 用于赛前 72h 高频采集（每 15min）

3. 扩展现有爬虫覆盖面
   - zgzcw/500.com 爬虫目前只采集有 match_code 的比赛
   - 增加模糊匹配逻辑（队名相似度 > 0.8 → 关联）

4. 赔率缺失时的降级策略
   - market 权重降为 0，Elo+Poisson+Players 重新归一化
   - 预测结果标注"无赔率参考，置信度低"
   - 前端展示时降低视觉权重（灰度或折叠）
```

**预计工时**：16 小时

---

### 🔴 致命-5：测试覆盖 < 5%

**现象**：仅 1 个 smoke test 文件（9 个测试函数），1951 行 `prediction_engine.py` 零覆盖。

**为什么致命**：任何修改（调整权重、新增特征、修复 bug）都可能无声引入回归。世界杯前必然会频繁调整模型参数。

**解决方案**：
```
优先级 1: 预测引擎核心逻辑
  - test_elo_prediction: 给定已知 Elo 差，验证输出概率
  - test_poisson_matrix: 验证比分矩阵和 = 1.0
  - test_dixon_coles: 验证 rho 修正对 0-0/1-1 的影响方向
  - test_fusion_weights: 验证权重归一化和动态调整逻辑
  - test_edge_cases: λ=0 时的 fallback、赔率缺失时的降级

优先级 2: 数据清洗
  - test_timezone_fix: 验证北京时间 → UTC 转换
  - test_team_alias: 验证别名映射正确性
  - test_zero_odds_fix: 验证 0.0 → NULL

优先级 3: API 端点
  - test_strategy_auth: 验证未付费用户被拒绝
  - test_admin_auth: 验证无 Key 被拒绝
```

**预计工时**：24 小时

---

### 🟡 严重-1：Walk-forward 泛化失败

**现象**：
```
训练集: 2018 (64场) → 测试集: 2022 (64场)
准确率: 51.6%
```
比在同一届上训练的 56.2% 下降 4.6%。

**为什么严重**：2026 世界杯在美加墨，气候/场地/时差与卡塔尔/俄罗斯完全不同，泛化风险更高。

**解决方案**：
1. 用 2014/2010/2006 世界杯数据扩充训练集（需手动收集）
2. 增加 leave-one-tournament-out 交叉验证
3. 融合权重按大洲/气候分组学习
4. 引入正则化（L2 惩罚）防止过拟合

**预计工时**：16 小时

---

### 🟡 严重-2：无预测快照锁定

**为什么严重**：赛后无法证明"赛前预测就是这个数字"。如果用户质疑"你们是不是赛后改了预测"，系统无法自证。

**解决方案**：
```
赛前 1 小时:
  1. 冻结所有输入数据（odds/team_stats/injuries）的 audit_id 列表
  2. 生成 prediction_snapshot JSON（含 hash）
  3. 存储到 prediction_snapshots 表（只读）
  4. 前端展示时显示 snapshot hash 和时间戳
```

**预计工时**：8 小时

---

### 🟡 中等-1：.env 存真实 Key ⏸️ 暂不处理

**现象**：`backend/.env` 中 ADMIN_API_KEY 和 FOOTBALL_DATA_API_KEY 明文。

**决策**：用户明确要求暂不处理 .env 问题。后续再执行。

**解决方案**（待执行）：
1. 所有 Key 改为环境变量注入
2. `.env` 只保留本地开发用配置
3. 生产部署用 `systemd EnvironmentFile` 或 Docker secrets
4. 轮换已泄露的 Key

**预计工时**：2 小时

---

### 🟡 中等-2：合规措辞风险 ✅ 已修复

**原现象**：25 处"推荐"和"建议"措辞违反合规要求。

**已实现的修复**（2026-05-15）：
- "推荐" → "模型估算"（16 处：strategy_pipeline/main/jingcai_predictor/app.js）
- "建议" → "参考方向"（5 处：prediction_report/app.js）
- "建议仓位" → "参考仓位"（1 处：jingcai_predictor）
- 免责声明中"不构成投注建议"保留（合规用语）
- "高价值"和"值得关注"在代码中未出现，无需替换

**验收**：`grep -rn '推荐' backend/strategy_pipeline.py backend/main.py` → 0 结果

---

## 5. 项目必须达到的水平

### 5.1 准确性目标

| 指标 | 当前 | 必须达到 | 验证方式 |
|------|------|---------|---------|
| 方向准确率（全部） | 48.6% | ≥ 55% | 30645 场有结果比赛的 `validation_engine.py` 批量验证 |
| 方向准确率（世界杯） | 56.2% (128场) | ≥ 58% | 2018+2022 回测 |
| 淘汰赛准确率 | 31.2% | ≥ 45% | Walk-forward 验证 |
| Brier Score | 0.2103 | ≤ 0.195 | 批量验证 |
| 概率校准度 | 未系统测量 | 预测 60% 的桶实际 ≈ 60% | 10 桶校准曲线 |

**为什么不是 62%？** PRD 的 62% 目标在当前数据条件下不现实。调整为更务实的阶段性目标：
- 55% 是「明显优于随机 + 优于大众平均水平(50%)」的底线
- 58% 世界杯是「给用户提供有价值参考」的门槛
- 淘汰赛 45% 是「不会让用户觉得是骗人」的最低线

### 5.2 数据覆盖目标

| 指标 | 当前 | 必须达到 |
|------|------|---------|
| 赔率覆盖率 | 30% (126/420) | ≥ 80% |
| 球员伤病数据 | 0 (默认值) | ≥ 60% 世界杯球队有真实数据 |
| xG 数据来源 | 回归估算 (R²=0.86) | ≥ 80% 球队有 FBref 直接采集的 xG |
| 世界杯球队 Elo 更新 | 手动快照 | 每周自动同步 ClubElo.com |

### 5.3 安全基线

| 要求 | 标准 |
|------|------|
| 所有写操作端点有认证 | Admin Key 或 JWT |
| 付费内容服务端校验 | 未付费用户策略字段返回 null |
| HTTPS 强制 | Nginx 层或 ENFORCE_HTTPS=1 |
| 密钥不在文件中存储 | 生产环境全用环境变量 |
| SQL 注入防护 | SQLAlchemy ORM 已满足 |
| XSS 防护 | escapeHtml() 已实现 |

### 5.4 可靠性基线

| 要求 | 标准 |
|------|------|
| 预测快照锁定 | 赛前 1h 冻结 + checksum |
| 数据库迁移工具 | Alembic |
| 核心模型有测试 | `prediction_engine.py` 关键函数 ≥ 80% 覆盖 |
| API 有冒烟测试 | 所有公开端点 |
| 调度器健康监控 | Prometheus metrics |
| 赔率采集失败告警 | 已实现（alert_manager.py）|

### 5.5 用户体验基线

| 要求 | 标准 |
|------|------|
| 比赛列表分页 | 每页 30 场，后端分页 |
| 预测数据标注新鲜度 | "X 分钟前更新" |
| 合规免责声明 | 每页底部 + 弹出时显示 |
| Tailwind CSS 预编译 | 替代 CDN 运行时编译 |

---

## 6. 分阶段改善路线

### Phase A：安全底线（1-2 天，立即执行） ✅ 已完成

```
优先级：所有 🔴 致命安全问题

A1. [4h] 策略 API 增加后端 license 鉴权 ✅
  ├── /api/matches/{id}/strategy 增加 get_current_active_user 依赖
  ├── 未付费用户 strategy 字段返回 null
  └── 赛后自动开放逻辑放后端

A2. [2h] 7 个 POST 端点增加认证 ✅
  ├── /api/live-odds/start → admin only
  ├── /api/live-odds/stop → admin only
  ├── /api/bet-nn/train → admin only
  └── 其余端点逐个审查

A3. [2h] 合规措辞全局替换 ✅
  ├── prediction_report.py: "推荐" → "模型估算"
  ├── strategy_pipeline.py: "高价值" → "正向偏离"
  └── 前端 app.js: 所有文案审查

A4. [2h] 密钥安全 ⏸️
  ├── .env 中 FOOTBALL_DATA_API_KEY 轮换
  ├── 生产部署脚本支持环境变量注入
  └── .gitignore 确认 .env 不在版本控制
```

### Phase B：模型可信度（3-5 天）

```
B1. [8h] 淘汰赛独立权重学习
  ├── 收集近 5 届世界杯淘汰赛数据 (≈80 场)
  ├── 补充欧冠淘汰赛 (≈200 场)
  ├── L-BFGS-B 优化专属权重
  └── Walk-forward 验证：目标 ≥ 45%

B2. [4h] 赔率缺失降级策略优化
  ├── 无赔率时 market=0，权重重新分配
  ├── 预测结果标注置信度等级
  └── 前端降级展示（灰色/折叠）

B3. [8h] 预测快照锁定实现
  ├── prediction_snapshots 表
  ├── 赛前 1h 调度器自动冻结
  ├── checksum (SHA256)
  └── API 返回 snapshot 信息

B4. [16h] 淘汰赛专项特征
  ├── 点球数据：国家队近 5 年点球命中率
  ├── 门将扑点率
  ├── 定位球进球占比
  └── 淘汰赛战术保守指数
```

### Phase C：数据覆盖（3-5 天）

```
C1. [8h] OddsHarvester 接入
  ├── pip install oddsharvester
  ├── Docker 或 subprocess 桥接
  ├── 覆盖 oddsportal.com 全球联赛赔率
  └── 目标：赔率覆盖率 30% → 60%

C2. [4h] Odds API 配置
  ├── 注册 the-odds-api.com
  ├── 赛前高频采集（每 15min）
  └── 目标：赔率覆盖率 60% → 80%

C3. [4h] 球员伤病自动采集
  ├── api-football.com injuries endpoint
  ├── 每天更新世界杯 48 队伤病
  └── 自动更新 key_injuries 和 squad_fatigue_index

C4. [4h] xG 数据直采
  ├── soccerdata FBref 直接爬取（非回归估算）
  ├── 每周更新
  └── 目标：80% 球队有真实 xG

C5. [4h] 世界杯球队 Elo 自动同步
  ├── ClubElo.com 爬虫或 API
  ├── 每周同步
  └── 种子数据更新
```

### Phase D：测试与可靠性（3-5 天）

```
D1. [16h] 核心模型单元测试
  ├── EloModel: Elo 差 → 胜率
  ├── PoissonModel: λ → 比分矩阵
  ├── DixonColes: rho 修正方向
  ├── Fusion: 权重归一化 + 动态调整
  └── 边界情况：λ=0, 赔率缺失, 空数据

D2. [8h] API 集成测试
  ├── 付费墙鉴权
  ├── Admin 端点认证
  └── 数据 CRUD 端点

D3. [4h] Alembic 数据库迁移
  ├── alembic init
  ├── 当前 schema 作为 baseline
  └── 后续改表通过 migration

D4. [4h] Walk-forward 自动化
  ├── 每次新结果录入后自动重算验证指标
  ├── 验证看板实时更新
  └── 漂移检测：准确率连续 10 场低于 45% → 告警
```

### Phase E：生产化部署（3-5 天）

```
E1. [4h] HTTPS + CDN
  ├── Nginx 配置 HTTPS（Let's Encrypt）
  ├── Cloudflare CDN 缓存静态资源
  └── ENFORCE_HTTPS=1

E2. [4h] Tailwind CSS 预编译
  ├── npm install tailwindcss
  ├── npx tailwindcss -i input.css -o tailwind.css --minify
  └── 替换 CDN link

E3. [4h] API 分页
  ├── GET /api/matches?page=1&page_size=30
  └── 前端分页组件

E4. [4h] 可观测性
  ├── Prometheus metrics (prometheus-fastapi-instrumentator)
  ├── 调度器健康指标
  ├── 赔率新鲜度 gauge
  └── Grafana dashboard

E5. [4h] 前端新鲜度标注
  ├── 预测显示"X 分钟前更新"
  ├── 赔率显示"采集于 HH:MM"
  └── 超过 30 分钟标黄，超过 2 小时标红
```

---

## 7. 验收标准

### 7.1 上线前必须通过

| 检查项 | 通过标准 | 验证方式 |
|--------|---------|---------|
| 🔒 付费墙有效 | 未付费用户 curl strategy 返回 null | `curl /api/matches/1/strategy` |
| 🔒 POST 端点鉴权 | 无 Key 返回 403 | `curl -X POST /api/live-odds/start` |
| 📊 准确率 ≥ 55% | 30645 场有结果比赛 direction_correct 占比 ≥ 55% | `validation_engine.py` 批量跑 |
| 📊 淘汰赛 ≥ 45% | 2018+2022 淘汰赛 walk-forward | `backtest_combined.py` |
| 📊 赔率覆盖 ≥ 80% | 即将进行比赛 odds_home IS NOT NULL 占比 ≥ 80% | SQL 查询 |
| 🧪 核心测试通过 | `pytest backend/tests/ -v` 全部通过 | CI 或手动 |
| 🗄️ 数据库可迁移 | `alembic upgrade head` 无报错 | 本地验证 |
| 🔐 HTTPS 生效 | curl 被重定向到 https | `curl -I http://domain` |
| 📄 免责声明可见 | 每页底部显示 + 弹窗显示 | 浏览器验证 |
| ⚡ 首屏 < 2s | LCP < 2s, Tailwind 预编译生效 | Lighthouse |

### 7.2 上线后持续监控

| 监控项 | 告警阈值 | 处理方式 |
|--------|---------|---------|
| 准确率漂移 | 连续 10 场 < 45% | 触发权重自动重学 |
| 赔率采集延迟 | 连续 3 次 > 15min | 切换备选源 |
| 预测重算延迟 | 赔率更新后 > 5min 未重算 | 重启调度器 |
| 调度器存活 | 心跳停止 > 2min | 自动重启 |
| API 响应时间 | p95 > 1s | 检查 SQLite 锁争用 |

---

## 附录 A：技术债清单

| 债务项 | 偿还优先级 | 预计工时 |
|--------|-----------|---------|
| 硬编码淘汰赛 stage_factor | B4 一并修复 | 0h |
| synthetic 赔率生成逻辑过于简单 | C1/C2 后移除 | 0h |
| `prediction_engine.py` 1951 行单文件 | D1 重构时拆分 | 8h |
| PlayerAdjustmentModel 数据全默认值 | C3 后自然解决 | 0h |
| FormAdjustmentModel 依赖字符串 "W/D/L" | 后续统一为 float 权重 | 4h |
| Elo 权重 0.04 几乎无用 | B1 重新学习后可能提升 | 0h |
| `isLocked()` 函数在前端重复 | 改为后端驱动后删除 | 0h |

## 附录 B：文件修改清单

```
需新建：
backend/alembic/ # 数据库迁移
backend/tests/test_prediction.py
backend/tests/test_fusion.py
backend/tests/test_cleaner.py
backend/prometheus.yml
static/tailwind.css (预编译)
✅ backend/integrations/cloakbrowser_bridge.py # cloakbrowser Python→Node.js 桥接
✅ backend/integrations/_cloak_scripts/fetch_pages.mjs # cloakbrowser 渲染脚本

需修改：
backend/main.py # 策略端点鉴权 ✅ + POST 鉴权 ✅ + 分页 ✅
backend/prediction_engine.py # 淘汰赛独立权重 ❌
backend/config.py # 环境变量优先 ❌
backend/jingcai_predictor.py # 合规措辞 ✅
backend/strategy_pipeline.py # 合规措辞 ✅
backend/prediction_report.py # 合规措辞 ✅
backend/scheduler.py # 快照锁定任务 ❌ + OddsHarvester 任务 ❌
backend/odds_collector.py # OddsHarvester 桥接 ✅ + cloakbrowser BetExplorer/Macau/HKJC ✅
backend/data_cleaner.py # xG 直采标记 ❌
static/app.js # 合规措辞 ✅ + 新鲜度标注 ❌ + 降级展示 ❌ + 分页(后端已实现) ❌
static/index.html # 免责声明 ❌ + 预编译 CSS ❌
backend/.env.example # 增加 API_FOOTBALL_KEY ❌
backend/requirements.txt # 增加 oddsharvester, alembic, prometheus-client ❌
backend/integrations/oddsharvester_bridge.py # cloakbrowser oddsportal 渲染 ✅

需删除/替换：
.env 中真实 Key (轮换后环境变量注入) ⏸️ 暂不处理
index.html 中 Tailwind CDN link ❌

---

> **本文档为活文档。每完成一个 Phase 后更新状态。所有工时估算基于单人全职，如需并行开发请适当压缩时间线。**
