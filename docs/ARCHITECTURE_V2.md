# 竞彩预测系统 — 架构修正方案 v2.0

> **版本**: v2.0（推翻 v1.0 的 NN 替代融合思路）  
> **日期**: 2026-05-15  
> **目标**: 以竞彩每期预测为核心场景，自动化迭代提升准确率  
> **设计原则**: 物理模型做基础 → 逻辑回归做融合 → 神经网络做修正  

---

## 目录

1. [总目标与衡量标准](#1-总目标与衡量标准)
2. [技术选型判断](#2-技术选型判断)
3. [系统架构总览](#3-系统架构总览)
4. [Layer 1：特征生成层](#4-layer-1特征生成层)
5. [Layer 2：逻辑回归融合层](#5-layer-2逻辑回归融合层)
6. [Layer 3：神经网络残差修正层](#6-layer-3神经网络残差修正层)
7. [Layer 4：策略输出层](#7-layer-4策略输出层)
8. [数据采集与清洗](#8-数据采集与清洗)
9. [自动化迭代机制](#9-自动化迭代机制)
10. [预期效果与里程碑](#10-预期效果与里程碑)
11. [后续升级路线](#11-后续升级路线)
12. [文件改动清单](#12-文件改动清单)

---

## 1. 总目标与衡量标准

### 1.1 场景定义

```
核心场景：中国竞彩足球每期预测
  - 每期 10-30 场比赛
  - 每周 2-3 期
  - 覆盖英超/西甲/德甲/意甲/法甲/日职/韩职/欧冠/国家队
  - 每场比赛都有竞彩官方赔率（sporttery.cn）
  - 6 种玩法：SPF / RQ / Score / Goals / Half / 混合过关

次级场景：世界杯/欧洲杯等大赛
  - 淘汰赛需要独立参数
```

### 1.2 衡量标准

| 指标 | 当前值 | 第一阶段目标 | 第二阶段目标 | 说明 |
|------|--------|------------|------------|------|
| **SPF 方向准确率** | 48.6% | ≥ 53% | ≥ 56% | 全量已结束比赛验证 |
| **SPF Brier Score** | 0.2103 | ≤ 0.200 | ≤ 0.190 | 概率校准度 |
| **淘汰赛准确率** | 31.2% | ≥ 42% | ≥ 48% | 独立权重 |
| **竞彩在售期准确率** | 未测量 | ≥ 55% | ≥ 58% | 近 10 期滚动 |
| **高置信度准确率** | ~70% (10场) | ≥ 65% (50+场) | ≥ 70% (100+场) | confidence=high 的比赛 |
| **赔率覆盖率（竞彩）** | 100% | 100% | 100% | 已满足 |
| **球员数据真实率** | ~5% | ≥ 60% | ≥ 80% | 非默认值占比 |

### 1.3 设计原则

```
1. 物理模型不可替代 — Elo/Poisson/赔率是经过学术验证的领域知识
2. 逻辑回归做融合 — 可解释 + 可正则化 + 小样本稳健
3. 神经网络做残差 — 只学「公式错了多少」，不学「比赛怎么预测」
4. 联赛分层 — 英超 ≠ 日职 ≠ 欧冠，不同联赛不同参数
5. 自动化闭环 — 数据采集 → 特征生成 → 权重学习 → 残差训练 → 预测输出
```

---

## 2. 技术选型判断

### 2.1 负二项分布：暂不引入

```
问题：泊松分布假设方差=均值（equidispersion），足球进球数据常存在
      过度离散（方差 > 均值），负二项可以处理。

不引入的理由：
  1. Dixon-Coles 修正已经解决了泊松最关键的缺陷（低比分相关性）
  2. 负二项破坏了双变量结构 — DC 修正建立在泊松基础上
  3. 引入负二项需要重建整个 score matrix 计算，改动量大
  4. 竞彩场景下比分预测准确率提升空间有限（31 选 1，本身极难）

未来评估时机：SPF 准确率达到 56% 后，如果比分/Brier 仍有明显提升
空间，可作为独立实验分支。
```

### 2.2 马尔可夫时序特征：作为特征引入

```
问题：当前 FormAdjustmentModel 用简单加权平均处理近期战绩，
      无法捕获状态转移模式（如"3 连胜后下一场胜率"vs"3 连败后反弹"）。

方案：不作为独立模型，而是提取马尔可夫衍生特征，喂给 Layer 2 逻辑回归。

特征设计：
  M1: P(win | 当前状态)    — 当前是连胜/连败/平局中，历史下一场胜率
  M2: P(draw | 当前状态)   — 同上，平局条件概率
  M3: 状态转移熵            — 球队状态稳定性（高熵=不稳定）
  M4: 反转概率              — 连败后反弹概率 vs 连胜后翻车概率

状态定义（简化）：取近 5 场结果序列，归约为 7 种状态
  {hot(WW+), warm(WD), neutral(DL混), cold(LL+), volatile(无规律), 
   rising(L→W), falling(W→L)}

实现方式：
  - SQL 查询历史数据，按球队计算条件概率表
  - 每周更新一次（变化慢）
  - 作为 4-7 维特征拼入逻辑回归输入
```

### 2.3 逻辑回归融合：替代当前线性加权

```
当前做法（线性加权）：
  P_final = norm(w1*Elo + w2*Poisson + w3*Players + w4*Market)
  问题：只能学 4 个权重，无法表达交互效应

升级方案（多项式逻辑回归）：
  log(P_home/P_draw) = beta0 + beta1*elo_logodds + beta2*poisson_logodds 
                       + beta3*player_factor + beta4*market_logodds
                       + beta5*(elo_diff * is_knockout)
                       + beta6*(odds_range * league_group)
                       + beta7*momentum_score + ...
                       (共 15-25 维特征)
  
  log(P_away/P_draw) = gamma0 + gamma1*elo_logodds + ... (对称)
  
  P = softmax([logodds_home, 0, logodds_away])

优势：
  1. 自然输出校准概率（softmax）
  2. 可加入交互项（elo_diff * league、odds * stage）
  3. L2 正则化防止过拟合
  4. 系数即权重，完全可解释
  5. scipy L-BFGS-B 可直接优化（已有依赖）
  6. 30000+ 样本对 25 维特征绰绰有余
```

### 2.4 神经网络：保留，变为残差修正器

```
当前角色：BetNN 平局检测器，输出被忽略
新角色：  残差修正器

改动：
  输入：逻辑回归的输出 + 额外特征（赔率变动、时序、联赛embedding）
  标签：实际结果 - 逻辑回归预测（残差向量）
  输出：修正量 Delta[h, d, a]
  最终：P = softmax(LR_output + alpha * Delta)
  
  其中 alpha 是信任系数：
    alpha = min(0.5, n_samples / 1000)  — 样本少时信任公式，样本多时信任NN
```

---

## 3. 系统架构总览

```
                              ┌──────────────────────┐
                              │   数据采集层          │
                              │  sporttery.cn 每日同步 │
                              │  FBref xG/球员每周更新 │
                              │  历史数据已导入 5330 场 │
                              └──────────┬───────────┘
                                         │
                              ┌──────────▼───────────┐
                              │  数据清洗层          │
                              │  时区统一/去重/别名   │
                              │  赔率去水/缺失标记    │
                              └──────────┬───────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
    ┌─────────▼──────────┐  ┌────────────▼──────────┐  ┌───────────▼──────────┐
    │ Layer 1: 特征生成   │  │ Layer 2: 逻辑回归融合  │  │ Layer 3: NN 残差修正  │
    │                    │  │                      │  │                      │
    │ EloModel ──────────┼──┤ log(P_home/P_draw) =  │  │ 输入: LR_output +    │
    │ PoissonModel ──────┼──┤   beta0 + sum(beta*X) │  │   时序+联赛+赔率变动  │
    │ PlayerModel ───────┼──┤   + 交互项 + 正则化    │  │                      │
    │ MarketModel ───────┼──┤                      │  │ 输出: Delta[h,d,a]   │
    │ FormMarkovModel ───┼──┤ 输出: 校准SPF概率     │  │ 最终: softmax(LR+aD) │
    │ RestAdvModel ──────┼──┤                      │  │                      │
    │ H2HModel ──────────┼──┤                      │  │                      │
    └────────────────────┘  └──────────┬───────────┘  └───────────┬──────────┘
                                      │                            │
                                      └──────────┬─────────────────┘
                                                 │
                                      ┌──────────▼───────────┐
                                      │ Layer 4: 策略输出层   │
                                      │                      │
                                      │ 校准(Platt Scaling)   │
                                      │ 边际计算(Edge)        │
                                      │ 过滤(4档风险)         │
                                      │ 仓位(Kelly)           │
                                      │ 风控检查              │
                                      └──────────┬───────────┘
                                                 │
                                      ┌──────────▼───────────┐
                                      │   前端展示            │
                                      │  SPF/RQ/Score/Goals/  │
                                      │  Half 概率 + EV +     │
                                      │  置信度 + 新鲜度      │
                                      └──────────────────────┘
```

### 3.1 各层职责

| 层 | 职责 | 输入 | 输出 | 为什么这一层 |
|----|------|------|------|------------|
| **Layer 1** 特征生成 | 把原始数据转为预测信号 | 球队Elo/战绩/球员/赔率 | 概率向量 + 特征向量 | 编码足球领域知识 |
| **Layer 2** 逻辑回归融合 | 学习最优特征组合方式 | 15-25 维特征向量 | 校准 SPF 概率 | 可解释 + 小样本稳健 |
| **Layer 3** NN 残差修正 | 修正 LR 的系统性偏差 | LR输出 + 高维特征 | 修正向量 Delta | 捕获非线性模式 |
| **Layer 4** 策略输出 | 把概率转为可操作输出 | 最终概率 + 赔率 | 策略建议 + 仓位 | 风险管理 |

---

## 4. Layer 1：特征生成层

### 4.1 模块清单

```
backend/
├── features/
│   ├── __init__.py
│   ├── elo_model.py          # Elo 实力基线（从 prediction_engine 提取）
│   ├── poisson_model.py      # 泊松攻防模型（从 prediction_engine 提取）
│   ├── player_model.py       # 球员状态修正
│   ├── market_model.py       # 赔率隐含概率（含去水）
│   ├── form_model.py         # 近期状态（升级为马尔可夫特征）
│   ├── rest_model.py         # 休息天数/赛程密度
│   ├── h2h_model.py          # 历史交锋（新增）
│   └── feature_builder.py    # 特征拼接 + 归一化
```

### 4.2 各模块详细设计

#### 4.2.1 EloModel（保持现有，改善数据源）

```
Elo 实力基线模型

改进点:
  1. Elo 值自动同步 ClubElo.com（替代手动快照）
  2. 俱乐部 Elo 扩展覆盖（现有 120 支 → 目标 300+ 支）
  3. 淘汰赛 Elo K-factor 调整（大赛权重更高）

输出特征:
  - elo_home: float           主队 Elo
  - elo_away: float           客队 Elo
  - elo_diff: float           Elo差值（归一化到 [-1, 1]）
  - elo_win_prob: float       纯 Elo 胜率
  - elo_draw_prob: float      纯 Elo 平率
  - elo_away_prob: float      纯 Elo 负率
  - is_heavy_favorite: bool   差距 > 200 分
```

#### 4.2.2 PoissonModel（保持现有，改善 lambda 计算）

```
泊松攻防模型（Dixon-Coles 修正）

改进点:
  1. xG 数据从回归估算 → FBref 直接采集（soccerdata 库）
  2. 13 步修正因子中，补全以下数据:
     - 球员伤病（soccerdata FBref）
     - 天气/场地（OpenWeatherMap 免费 API）
     - 战术风格标签（手动标注 48 支世界杯队 + 50 支俱乐部）
  3. 淘汰赛 lambda 从硬编码 → 从数据学习

输出特征:
  - lambda_home: float        主队期望进球
  - lambda_away: float        客队期望进球
  - poisson_home_prob: float  泊松主胜概率
  - poisson_draw_prob: float  泊松平局概率
  - poisson_away_prob: float  泊松客胜概率
  - goal_total_exp: float     期望总进球
  - goal_diff_exp: float      期望进球差
```

#### 4.2.3 PlayerModel（改善数据质量）

```
球员状态修正模型

改进点:
  1. 数据源从手动 → soccerdata FBref 自动采集
     - 出场时间、进球、xG、助攻
     - 伤病报告
  2. 核心球员定义：按出场时间 + xG 贡献自动排序 top 5
  3. 疲劳指数：近 30 天出场分钟数 / 最大可能分钟数

输出特征:
  - home_availability: float  核心球员可用率 (0-1)
  - away_availability: float
  - home_fatigue: float       疲劳指数 (0-1, 越高越疲劳)
  - away_fatigue: float
  - home_injury_count: int    伤病/停赛人数
  - away_injury_count: int
  - home_star_missing: bool   是否有明星球员缺阵
  - away_star_missing: bool
```

#### 4.2.4 MarketModel（新增赔率去水）

```
市场赔率模型

改进点（关键）:
  1. 竞彩赔率去水 — 竞彩返奖率 ~71%，系统性压低赔率
  
  去水算法:
    overround = 1/odds_h + 1/odds_d + 1/odds_a   # 通常 ≈ 1.40
    P_home = (1/odds_h) / overround
    P_draw = (1/odds_d) / overround
    P_away = (1/odds_a) / overround

  2. 多源赔率对比 — 当 zgzcw/500.com 也返回赔率时，取平均
  3. 赔率变动特征 — 初盘 vs 即时盘的变化幅度

输出特征:
  - market_home_prob: float   去水后主胜隐含概率
  - market_draw_prob: float   去水后平局隐含概率
  - market_away_prob: float   去水后客胜隐含概率
  - odds_home_raw: float      原始赔率
  - odds_draw_raw: float
  - odds_away_raw: float
  - overround: float          抽水率（越低越可信）
  - odds_move_home: float     赔率变动（即时/初盘 - 1）
  - odds_move_draw: float
  - odds_move_away: float
  - source_count: int         赔率来源数量（越多越可信）
```

#### 4.2.5 FormMarkovModel（新增，替代 FormAdjustmentModel）

```
马尔可夫时序状态模型

状态定义（近 5 场结果序列 → 7 种状态）:
  hot:       近 5 场 >= 4 胜
  warm:      近 5 场 >= 3 胜 + 1 平
  neutral:   胜负平混合，无明显趋势
  cold:      近 5 场 >= 3 负
  rising:    近 3 场趋势向上 (L/D → W)
  falling:   近 3 场趋势向下 (W/D → L)
  volatile:  胜负交替，无规律 (WLWLW)

特征计算（从历史数据统计条件概率）:
  P(下一场=W | 当前状态=S) = count(W after S) / count(S)

输出特征:
  - form_state: str           当前状态标签
  - form_win_prob: float      P(win | state)
  - form_draw_prob: float     P(draw | state)
  - form_lose_prob: float     P(lose | state)
  - form_momentum: float      动量分数 (rising=+0.2, falling=-0.2, etc.)
  - form_stability: float     稳定性 (hot/cold 高, volatile 低)
  - streak_length: int        当前连续同结果的场次
```

#### 4.2.6 RestAdvModel（保持现有，结构化输出）

```
休息与赛程优势模型

输出特征:
  - rest_days_home: int       主队休息天数
  - rest_days_away: int       客队休息天数
  - rest_advantage: int       主队多休息天数（正=优势）
  - is_3rd_match_in_7days: bool  是否 7 天内第 3 场（疲劳警告）
```

#### 4.2.7 H2HModel（新增）

```
历史交锋模型

从数据库查询两队历史交锋记录，计算:
  - h2h_total: int            历史交锋总场次
  - h2h_home_win_pct: float   主队在历史交锋中的胜率
  - h2h_draw_pct: float       平局率
  - h2h_recent_win_pct: float 近 3 次交锋主队胜率
  - h2h_avg_goals: float      历史交锋场均总进球
  - is_first_meeting: bool    是否首次交锋

降级策略:
  h2h_total < 3 → 所有 h2h 特征设为 0（让 LR 忽略）
```

#### 4.2.8 FeatureBuilder（特征拼接器）

```
特征拼接器 — 把所有模型输出组装成 LR 输入向量

特征清单（共 38 维）:

类别 A: Elo 特征 (7维)
  elo_diff, elo_home_win, elo_draw, elo_away, 
  is_heavy_favorite, is_heavy_underdog, elo_tier_diff

类别 B: 泊松特征 (6维)
  lambda_home, lambda_away, lambda_diff, 
  poisson_home, poisson_draw, poisson_away, goal_total_exp

类别 C: 球员特征 (4维)
  home_availability, away_availability, 
  availability_diff, injury_impact

类别 D: 市场特征 (6维)
  market_home_prob, market_draw_prob, market_away_prob,
  overround, max_odds_move, source_count

类别 E: 时序特征 (5维)
  form_win_prob, form_draw_prob, form_momentum, 
  form_stability, streak_length

类别 F: 元特征 (3维)
  rest_advantage, is_knockout, is_derby

交互特征（自动生成，~10维）:
  elo_diff * is_knockout
  overround * league_group (one-hot → 6维)
  form_momentum * rest_advantage
  market_home_prob * source_count

总计: 28 + 10 = 38 维 → L1 正则化自动筛选有效特征
```

---

## 5. Layer 2：逻辑回归融合层

### 5.1 为什么是逻辑回归而非当前线性加权

```
当前线性融合:
  P = normalize(w1*Elo + w2*Poisson + w3*Players + w4*Market)
  
  局限:
  - 只有 4 个参数（3 个自由度），学习能力极弱
  - 无法表达"英超比赛中 market 应该权重更高"这种条件逻辑
  - 无法表达"淘汰赛时对泊松模型的信任应该打折"
  - 概率不在 log-odds 空间操作，归一化方式粗暴

逻辑回归融合:
  log(P_home/P_draw) = beta · X_features    (X 有 30+ 维)
  log(P_away/P_draw) = gamma · X_features
  P = softmax([logodds_home, 0, logodds_away])
  
  优势:
  - 30+ 个参数，但每个都有意义（L1 正则化自动特征选择）
  - 在 log-odds 空间操作，数学上更合理
  - 交互项可以编码条件逻辑
  - 系数 = 特征贡献，完全可解释
  - 30000 样本对 30 参数完全足够
```

### 5.2 模型设计

```
MultinomialLogisticFusion — 多项式逻辑回归融合器

训练:
  1. 从数据库读取所有已结束比赛
  2. 对每场比赛运行 Layer 1 全部子模型 → 38 维特征向量
  3. 标签: y in {0(home), 1(draw), 2(away)}
  4. 优化: L-BFGS-B 最小化 cross-entropy loss + L1 penalty
  5. 输出: 两个系数向量 beta_home(38维), beta_away(38维)

推理:
  1. 新比赛 → Layer 1 生成 38 维特征
  2. logodds_home = beta_home · X
  3. logodds_away = beta_away · X  
  4. P = softmax([logodds_home, 0, logodds_away])

正则化策略:
  - L1 (Lasso): 自动把不重要的特征系数压为 0
  - 正则化强度 lambda 通过 5-fold 交叉验证在历史数据上选择
  - 结果: 30+ 维特征 → 实际有效 15-20 维

分层学习:
  - 全局模型: 所有比赛训练一个基础 LR
  - 联赛模型: 每个联赛组微调（warm-start from 全局模型）
  - 淘汰赛模型: 独立训练（样本少，用更强正则化）

实现:
  scipy.optimize.minimize(method='L-BFGS-B')
  已有依赖，无需新增库
```

### 5.3 权重存储

```
替代当前的 DEFAULT_WEIGHTS = {"elo": 0.04, ...}
改为:

class LogisticFusionWeights:
    """逻辑回归融合权重"""
    
    # 全局模型
    coef_home: np.ndarray      # shape (38,)  主胜 log-odds 系数
    coef_away: np.ndarray      # shape (38,)  客胜 log-odds 系数
    intercept_home: float
    intercept_away: float
    l1_penalty: float          # 使用的正则化强度
    
    # 联赛微调模型（可选）
    league_models: Dict[str, 'LogisticFusionWeights']
    
    # 淘汰赛模型
    knockout_coef_home: np.ndarray
    knockout_coef_away: np.ndarray
    
    # 元信息
    trained_at: datetime
    sample_count: int
    cross_entropy: float       # 训练集上的 loss
    
    def predict(self, features: np.ndarray) -> Dict[str, float]:
        """推理：特征 → 概率"""
        ...
    
    def explain(self, features: np.ndarray) -> Dict[str, float]:
        """可解释性：每个特征的贡献"""
        contributions = {}
        for i, name in enumerate(FEATURE_NAMES):
            contrib_home = self.coef_home[i] * features[i]
            contrib_away = self.coef_away[i] * features[i]
            contributions[name] = {
                "toward_home": contrib_home,
                "toward_away": contrib_away,
            }
        return contributions
```

---

## 6. Layer 3：神经网络残差修正层

### 6.1 角色重新定义

```
旧 BetNet:  输入主引擎输出 → 预测胜平负 → 用作平局检测 → 结果被忽略
新 BetNet:  输入 LR 输出 + 高维特征 → 预测 LR 的残差 → 修正最终概率

为什么残差学习:
  LR 已经学到 ~53% 准确率（预期）
  NN 只需要修复剩下的 ~47%
  残差学习比完整学习需要的样本少一个数量级
```

### 6.2 模型设计

```
ResidualBetNet — 残差修正网络

架构不变（3层MLP），改训练目标:

旧目标（分类）:
  y_true = [1, 0, 0]       # one-hot 实际结果
  y_pred = NN(features)     # 预测评分
  loss = BCE(y_pred, y_true)

新目标（残差回归）:
  lr_output = LR.predict(features)           # LR 预测概率 [0.45, 0.28, 0.27]
  y_true_onehot = [1, 0, 0]                  # 实际结果 one-hot
  residual = y_true_onehot - lr_output        # [0.55, -0.28, -0.27]
  delta = NN(lr_output, extra_features)       # 预测残差
  loss = MSE(delta, residual)                 # 回归 loss

推理:
  lr_output = LR.predict(features)
  delta = NN(lr_output, extra_features)
  alpha = min(0.5, n_training_samples / 1000)  # 信任系数
  final = softmax(lr_output + alpha * delta)

输入特征（与 LR 不同，可以更多维）:
  - LR 输出: [lr_home, lr_draw, lr_away]  (3维)
  - LR 置信度特征: max_prob, entropy  (2维)
  - 赔率变动时序: 过去 24h 的 odds 走势 (6维)
  - 联赛 embedding: 8 维可学习向量
  - 时序特征: form 状态 one-hot (7维)
  共 ~26 维

训练参数:
  - 隐藏层: [64, 32, 16]（保持不变）
  - Dropout: 0.2 → 0.3（残差学习更容易过拟合）
  - Early stopping: patience=10（比原来宽松）
  - Batch size: 64（保持不变）
  - Optimizer: AdamW (weight decay=1e-4)
```

### 6.3 训练节奏

```
触发条件：
  - 新增 >= 100 场有结果的比赛 → 触发增量训练
  - 或每周末定时训练（无论新增多少）

训练流程：
  1. 先用最新 LR 模型对所有历史比赛重跑推理
  2. 计算 LR 残差
  3. 用残差训练 NN
  4. 在验证集上评估: NN 修正后的 Brier Score 是否比纯 LR 更低
  5. 如果更低 → 部署新 NN 权重
  6. 如果更高 → 保持旧权重，增大 alpha 衰减系数

alpha 信任系数动态调整：
  alpha = min(0.5, n_samples / 1000) * validation_improvement_ratio
  - 样本 < 1000: alpha < 0.5，保守
  - 样本 > 2000: alpha → 0.5
  - validation_improvement_ratio: 验证集上 NN 修正带来的 Brier 改进比例
```

---

## 7. Layer 4：策略输出层

### 7.1 保持现有架构

当前已有且不需要改动的：

```
strategy_pipeline.py  — 校准→边际→过滤→仓位→风控
calibrator.py         — Platt Scaling
edge_calculator.py    — 边际/EV 计算
position_sizer.py     — Kelly 仓位
risk_manager.py       — 风控检查
tiered_strategy.py    — 分层策略
```

### 7.2 接入点改动

```
# 改动前（旧接口）
pipeline = StrategyPipeline(risk_tier="balanced")
picks = pipeline.generate(
    predictions=prediction_result,         # 来自 PredictionEngine
    odds_home=1.80, odds_draw=3.50, odds_away=4.20
)

# 改动后（新接口）
pipeline = StrategyPipeline(risk_tier="balanced")
picks = pipeline.generate(
    lr_probs=logistic_fusion.predict(features),    # Layer 2 输出
    nn_delta=residual_nn.predict(lr_probs, extra),  # Layer 3 输出
    features=features,                              # 用于可解释性
    odds_home=1.80, odds_draw=3.50, odds_away=4.20,
)
```

---

## 8. 数据采集与清洗

### 8.1 数据源矩阵

| 数据 | 来源 | 频率 | 方式 | 优先级 |
|------|------|------|------|--------|
| 竞彩比赛+赔率 | sporttery.cn API | 每日 08:00 | 已有 | ✅ 已就绪 |
| 竞彩期号 | sporttery.cn API | 09:00/15:00 | 已有 | ✅ 已就绪 |
| 百家欧赔 | zgzcw.com 爬虫 | 每 30min | 已有 | ✅ 已就绪 |
| 百家欧赔 | 500.com 爬虫 | 每 2h | 已有 | ✅ 已就绪 |
| 全球赔率+JS页面 | cloakbrowser (Playwright stealth) | 每日 | **已安装** | ✅ 已就绪 |
| 球员数据 | soccerdata FBref | 每周 | **新增** | 🔴 P0 |
| xG/xGA | soccerdata FBref | 每周 | **改善**（替代回归） | 🔴 P0 |
| ClubElo | ClubElo.com | 每周 | **新增** | 🔴 P0 |
| 历史交锋 | 本库 SQL 查询 | 实时 | **新增** | 🟡 P1 |
| 全球赔率 | OddsHarvester | 每日 | **新增** | 🟡 P1 |
| 天气数据 | OpenWeatherMap | 赛前 24h | **新增** | 🟢 P2 |
| 阵容/首发 | FBref Match Report | 赛前 2h | **新增** | 🟢 P2 |

### 8.2 赔率去水 — 竞彩专项

```
竞彩赔率去水 — 去除 ~29% 的抽水

竞彩返奖率约 71%，即每 1 元投注，期望返还 0.71 元。
直接用 1/odds 算概率会系统性高估每个结果的概率。

Multiplicative method:
  假设 margin 按比例分摊到每个结果

算法:
  overround = 1/odds_h + 1/odds_d + 1/odds_a
  P_h = (1/odds_h) / overround
  P_d = (1/odds_d) / overround
  P_a = (1/odds_a) / overround

示例:
  竞彩: 主胜 2.10, 平 3.40, 客胜 3.20
  overround = 1/2.10 + 1/3.40 + 1/3.20 = 0.476 + 0.294 + 0.313 = 1.083
  去水后: P_h = 0.476/1.083 = 43.9%, P_d = 27.2%, P_a = 28.9%
```

---

## 9. 自动化迭代机制

### 9.1 每日节奏

```
05:00  xG 估算 + FBref 数据采集
05:30  模型复盘（已有 model_audit daily）
06:30  BetNN 残差训练（每日增量）
08:00  sporttery.cn 同步比赛 + 赔率 + 自动生成预测 ← 用户看到新预测
09:00  竞彩期号同步
15:00  竞彩期号同步
```

### 9.2 每周节奏（周一）

```
06:00  深度复盘（已有 weekly_audit）
06:05  联赛分层 LR 权重学习（新增）
  ├── 全局模型重训：全部 30000+ 场比赛
  ├── 联赛微调：英超/西甲/德甲/意甲/法甲/日职/韩职各自微调
  └── 淘汰赛独立模型：世界杯+欧冠淘汰赛
06:10  交叉验证：新权重 vs 旧权重 Brier Score 对比
06:15  自愈闭环（已有 self_heal）
  ├── 漂移检测：准确率变化 > 3% → 触发
  ├── 权重更新：部署新 LR 权重
  └── 预测重生成：所有 upcoming 比赛重跑
06:30  BetNN 残差全量重训（每周一次全量）
06:45  子模型训练（半场/比分/让球，已有）
```

### 9.3 触发条件

```
LR 重训触发：
  - 每周一例行
  - 或新比赛 >= 500 场
  - 或漂移检测触发（连续 10 场准确率 < 45%）

BetNN 重训触发：
  - 每日增量（新增 >= 10 场有结果比赛）
  - 每周一全量（防止增量训练漂移）
  - LR 重训后必须重训 NN（因为残差定义变了）

预测重生成触发：
  - LR 权重更新后
  - 赔率更新后
  - 球员/伤病数据更新后
  - 赛前 1 小时（最后的预测快照）
```

### 9.4 权重版本管理

```
backend/data/weights/
├── lr/
│   ├── global_v3_20260515.json        # 全局 LR 权重
│   ├── league_epl_v3_20260515.json    # 英超微调
│   ├── league_jleague_v3_20260515.json
│   └── knockout_v2_20260515.json      # 淘汰赛
├── nn/
│   ├── residual_best_20260515.pt      # NN 残差权重
│   └── residual_last_20260515.pt
├── sub_models/                         # 已有子模型权重
│   ├── halftime/
│   ├── score/
│   └── handicap/
└── version_log.json                    # 版本变更记录
```

---

## 10. 预期效果与里程碑

### 10.1 分阶段目标

```
Phase 1: 基础升级（第 1 周）
  内容: 赔率去水 + LR 融合替代线性加权 + 联赛分层
  改动: ~300 行
  预期: SPF 准确率 48.6% → 51-53%
  原因: 赔率去水消除系统性偏差 + LR 比线性加权多 10 倍参数

Phase 2: 特征补全（第 2 周）
  内容: 球员真实数据 + 马尔可夫时序 + H2H 特征
  改动: ~400 行
  预期: SPF 准确率 53% → 54-55%
  原因: 球员/时序/H2H 提供 LR 之前没有的有效信号

Phase 3: 残差 NN（第 3 周）
  内容: BetNN 改为残差修正 + 接入最终预测
  改动: ~200 行
  预期: SPF 准确率 55% → 55-56%
  原因: NN 残差修正主要修复少数困难样本，整体提升有限但关键比赛提升明显

Phase 4: 淘汰赛专项（第 4 周）
  内容: 淘汰赛独立 LR + 淘汰赛专属特征（点球/门将/定位球）
  改动: ~200 行
  预期: 淘汰赛准确率 31% → 42-48%
  原因: 独立参数 + 淘汰赛专属特征

Phase 5: 持续优化（世界杯前）
  内容: Elo 自动同步 + xG 直采 + 天气特征 + 首发预测
  改动: ~300 行
  预期: 整体 56% → 57-58%
```

### 10.2 里程碑

```
M1 (第 1 周末): LR 融合上线，准确率 >= 51%
M2 (第 2 周末): 全部特征就绪，准确率 >= 54%
M3 (第 3 周末): NN 残差修正上线，准确率 >= 55%
M4 (第 4 周末): 淘汰赛 >= 42%，整体系统稳定
M5 (世界杯前): 整体 >= 56%，自动化闭环完整
```

---

## 11. 后续升级路线

### 11.1 短期（世界杯前）

| 升级项 | 预期提升 | 复杂度 |
|--------|---------|--------|
| OddsHarvester 接入 → 赔率源从 3 个到 10+ | 提高赔率可信度 | 中 |
| 竞彩混合过关组合优化 | 新增玩法 | 低 |
| 赛前 1h 预测快照锁定 | 信任度 | 低 |
| 前端新鲜度标注 | 用户体验 | 低 |

### 11.2 中期（世界杯期间）

| 升级项 | 预期提升 | 复杂度 |
|--------|---------|--------|
| 实时赔率 WebSocket 推送 | 临场决策 | 高 |
| 比赛中动态更新预测（Live Model） | 新场景 | 高 |
| 用户行为反馈闭环（点击/兑换 → 模型偏好） | 个性化 | 中 |
| 必发交易所成交量特征 | 资金流向信号 | 中 |

### 11.3 长期（世界杯后）

| 升级项 | 条件 | 说明 |
|--------|------|------|
| 负二项分布替代泊松（比分预测） | SPF >= 56% 后 | 过度离散修正 |
| XGBoost 特征筛选 | 样本 >= 50000 场 | 自动发现有效交互项 |
| Transformer 时序模型 | 样本 >= 100000 场 | 长序列依赖学习 |
| 端到端 NN 融合 | 样本 >= 100000 场 | 只有在这个量级才安全 |

---

## 12. 文件改动清单

### 12.1 新建文件

```
backend/features/
├── __init__.py
├── elo_model.py              # 从 prediction_engine 提取 EloModel
├── poisson_model.py          # 从 prediction_engine 提取 PoissonModel
├── player_model.py           # 从 prediction_engine 提取 PlayerAdjustmentModel
├── market_model.py           # 从 prediction_engine 提取 MarketModel + 去水
├── form_markov_model.py      # 新建：马尔可夫时序特征
├── rest_model.py             # 提取 ScheduleDensityModel
├── h2h_model.py              # 新建：历史交锋
├── feature_builder.py        # 新建：特征拼接 + 归一化

backend/fusion/
├── __init__.py
├── logistic_fusion.py       # 新建：多项式逻辑回归融合器
├── fusion_trainer.py         # 新建：LR 训练 + 交叉验证
└── fusion_weights.py         # 新建：权重存储/加载/版本管理

backend/data/
└── weights/                   # 新建：权重文件目录
    └── .gitkeep

backend/tests/
├── test_feature_builder.py
├── test_logistic_fusion.py
├── test_market_model.py
└── test_form_markov.py
```

### 12.2 修改文件

```
backend/prediction_engine.py
  - 提取子模型到 features/ 目录
  - PredictionEngine.predict() 改为调用 LR + NN
  - 保留回测兼容接口

backend/bet_nn.py
  - 训练目标: 分类 → 残差回归
  - 输入: 增加 LR 输出和额外特征
  - 推理: 输出残差向量而非预测评分

backend/weight_learner.py
  - 学习目标: L-BFGS-B 优化 Brier → 优化 cross-entropy + L1
  - 输出: 从 4 维权重字典 → 38 维系数向量
  - 增加联赛分层学习

backend/fusion_strategy.py
  - 改为调用 LR 融合而非线性加权

backend/scheduler.py
  - 新增 form_markov 计算任务（每周一 06:05）
  - 新增 h2h 计算任务（每周一 06:03）
  - BetNN 训练频率调整

backend/sporttery_sync.py
  - 写入赔率时自动计算去水概率

static/app.js
  - 前端展示：显示置信度来源（LR 融合 + NN 修正）
```

### 12.3 保留不变的文件

```
✅ backend/main.py               # API 路由（接口不变，底层换）
✅ backend/models.py             # 数据库模型
✅ backend/admin.py              # 管理后台
✅ backend/auth.py               # 认证
✅ backend/config.py             # 配置
✅ backend/strategy_pipeline.py  # 策略管线
✅ backend/calibrator.py         # 校准器
✅ backend/edge_calculator.py    # 边际计算
✅ backend/position_sizer.py     # 仓位
✅ backend/risk_manager.py       # 风控
✅ backend/data_cleaner.py       # 数据清洗
✅ backend/model_audit.py        # 模型审计（微调阈值）
✅ backend/validation_engine.py  # 验证引擎
✅ backend/scheduler.py          # 调度器（增任务，不动结构）
✅ backend/odds_collector.py     # 赔率采集
✅ backend/sporttery_sync.py     # 竞彩同步
✅ backend/jingcai_predictor.py  # 竞彩预测器
✅ backend/license_manager.py    # 卡密系统
✅ backend/sub_model_*.py        # 子模型
✅ backend/draw_classifier.py    # 平局分类器
```

---

## 附录：关键公式汇总

### A. 赔率去水（Multiplicative Method）

```
overround = sum(1/odds_i)
P_i = (1/odds_i) / overround
```

### B. 多项式逻辑回归

```
log(P_home / P_draw) = beta0 + sum(beta_j * X_j)
log(P_away / P_draw) = gamma0 + sum(gamma_j * X_j)
P = softmax([logodds_h, 0, logodds_a])
```

### C. NN 残差修正

```
lr_output = LR(X)
residual = y_true_onehot - lr_output
delta = NN(lr_output, extra_features)
final = softmax(lr_output + alpha * delta)
alpha = min(0.5, n_samples / 1000)
```

### D. 马尔可夫状态条件概率

```
state = classify(recent_5_results)   # → {hot, warm, neutral, cold, rising, falling, volatile}
P(outcome | state) = count(outcome after state) / count(state)
```

---

> **本文档是 v2.0 架构方案。推翻 v1.0 中「NN 替代融合」的思路，改为「物理模型 → 逻辑回归融合 → NN 残差修正」三层递进。所有改动与现有代码兼容，不推倒重来。**
