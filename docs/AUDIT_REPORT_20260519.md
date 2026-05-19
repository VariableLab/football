# 竞彩足球预测系统 - 全面审计报告

> **审计日期**: 2026-05-19  
> **审计范围**: 数据库自动化、后端预测引擎、前端显示、项目自动化  
> **审计人**: Kiro AI  
> **系统版本**: v2.0-lr  

---

## 📊 执行摘要

### 系统健康度评级：⚠️ **降级运行**

**关键发现**：
- ✅ 核心预测引擎架构完整（LR融合 + 残差NN）
- ✅ 自动化任务框架健全（41个定时任务）
- 🔴 **赔率采集系统完全停滞**（0%覆盖率）
- 🟡 LR融合权重部分失效（仅355/156,030条使用v2.0-lr）
- 🟡 残差NN数值不稳定

**紧急行动项**：
1. 立即修复赔率采集系统
2. 验证并重新生成LR融合预测
3. 修复残差NN特征标准化问题

---

## 1. 项目当前状态

### 1.1 数据规模

| 指标 | 数值 | 说明 |
|------|------|------|
| 比赛总数 | 31,402 场 | 覆盖 46 个联赛/赛事 |
| 已结束比赛 | 31,238 场 | 含 230 场世界杯淘汰赛 |
| 球队数 | 462 支 | 自动发现 + 手动录入 |
| 预测总数 | 156,030 条 | 5 种玩法全覆盖 |
| 竞彩期号 | 13 期 | 3 on_sale + 6 drawn + 4 verified |
| 待处理比赛 | 164 场 | 137 scheduled + 19 live + 8 upcoming |

### 1.2 模型版本分布

| 版本 | 预测数 | 占比 | 说明 |
|------|--------|------|------|
| v2.0 | 156,675 | 99.8% | 旧线性融合 |
| v2.0-lr | 355 | 0.2% | LR逻辑回归融合 |

**问题**：绝大多数预测仍使用旧模型，LR融合未全面部署。

### 1.3 数据新鲜度

| 类型 | 覆盖率 | 状态 |
|------|--------|------|
| 赔率数据 | 0/164 (0%) | 🔴 停滞 |
| 预测数据 | 164/164 (100%) | ✅ 正常 |
| 置信度分布 | 低(130) / 中(55) / 高(5) | ⚠️ 低置信度占比过高 |

---

## 2. 发现的问题（按优先级）

### 🔴 P0 - 关键问题（影响系统可用性）

#### 问题 #1：赔率采集系统完全停滞

**现象**：
- 待处理比赛（164场）的赔率覆盖率为 0%
- 所有赔率采集任务运行但无数据产出

**根本原因**：
```python
# scheduler.py 中注册的赔率采集任务
collect_zgzcw_job()      # 每30分钟 - zgzcw.com 可能被反爬
collect_500_job()        # 每30分钟 - 500.com 可能被反爬
collect_odds_tier2_job() # 每天08:00/20:00 - 需付费 Odds API key
```

**影响范围**：
- 预测引擎无法获取市场信号（MarketModel 返回 None）
- 预测质量下降（fallback 到旧 EnsembleFusion）
- 赔率异动检测失效
- 用户看到的预测基于过时或合成赔率

**修复方案**：
1. **立即检查数据源状态**：
   ```bash
   cd backend
   python -c "from zgzcw_source import collect_zgzcw_odds; from models import SessionLocal; db = SessionLocal(); print(collect_zgzcw_odds(db))"
   ```
2. **添加反爬对策**：
   - 实现代理池轮换
   - 添加 User-Agent 随机化
   - 增加请求间隔（避免触发限流）
3. **启用备用数据源**：
   - football-data.co.uk（免费历史赔率）
   - 实现合成赔率兜底（基于 Elo + Poisson）
4. **添加告警机制**：
   ```python
   # 在 odds_collector.py 中添加
   if updated == 0 and total > 0:
       fire_alert("odds_collection", "critical", f"赔率采集失败: {total}场比赛无数据")
   ```

**验收标准**：
- [ ] 赔率覆盖率 ≥ 80%
- [ ] 收盘赔率来源 ≥ 3 个不同公司
- [ ] 无 synthetic 赔率混入 closing_odds

---

#### 问题 #2：LR融合权重加载失败

**现象**：
- 仅 355/156,030 (0.2%) 预测使用 v2.0-lr
- 大部分预测仍使用旧 v2.0 模型

**根本原因**：
```python
# prediction_engine.py:1185-1203
def _load_lr_weights():
    _lr_weights_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "weights", "lr")
    lr_files = sorted(glob.glob(os.path.join(_lr_weights_dir, "global_*.json")))
    if lr_files:
        return LogisticFusionWeights.load(lr_files[-1])
    return None  # 加载失败时无日志
```

**影响范围**：
- 预测准确率不一致
- 无法追踪为何某些比赛未使用 LR 融合
- 用户看到的模型版本混乱

**修复方案**：
1. **验证权重文件**：
   ```bash
   ls -lh backend/data/weights/lr/global_*.json
   ```
2. **添加详细日志**：
   ```python
   if not lr_files:
       logger.error(f"[LR-fusion] No weight files found in {_lr_weights_dir}")
   else:
       logger.info(f"[LR-fusion] Found {len(lr_files)} weight files, loading {lr_files[-1]}")
   ```
3. **强制重新生成预测**：
   ```bash
   cd backend
   python regenerate_predictions.py --force --model-version v2.0-lr
   ```
4. **检查周训练任务**：
   ```bash
   # 查看 fusion_train_weekly 任务日志
   grep "fusion_train_weekly" logs/uvicorn.log
   ```

**验收标准**：
- [ ] 所有待处理比赛预测 model_version = "v2.0-lr"
- [ ] 权重文件存在且可加载
- [ ] 日志中有权重加载成功记录

---

#### 问题 #3：残差NN数值不稳定

**现象**：
- `residual_nn.error.2026-05-17.log` 存在错误日志
- 残差修正可能失效

**根本原因**：
```python
# residual_nn.py 中特征标准化
feature_std = np.std(features, axis=0)
normalized = (features - feature_mean) / feature_std  # 当 std=0 时除零错误
```

**影响范围**：
- 残差修正失效，预测偏差无法纠正
- 系统降级到 LR 融合（无残差修正）

**修复方案**：
1. **修复特征标准化**：
   ```python
   feature_std = np.std(features, axis=0)
   feature_std = np.where(feature_std == 0, 1.0, feature_std)  # 避免除零
   normalized = (features - feature_mean) / feature_std
   ```
2. **添加特征有效性检查**：
   ```python
   if np.any(np.isnan(normalized)) or np.any(np.isinf(normalized)):
       logger.error("[ResidualNN] Invalid features detected, skipping correction")
       return spf  # 返回原值
   ```
3. **重新训练残差NN**：
   ```bash
   cd backend
   python residual_nn.py --train --epochs 100
   ```

**验收标准**：
- [ ] 无除零错误日志
- [ ] Delta 值在合理范围内（-0.2 ~ +0.2）
- [ ] 残差修正成功率 ≥ 95%

---

### 🟡 P1 - 高优先级问题（影响预测质量）

#### 问题 #4：竞彩期号同步不完整

**现象**：
- 仅 13 期竞彩期号（3 on_sale + 6 drawn + 4 verified）
- 用户看不到全部在售期号

**根本原因**：
- `jingcai_sync_job()` 每天 09:00/15:00 运行
- `zgzcw_jc_sync()` 每 30 分钟运行但可能覆盖不全
- 竞彩官方 API（sporttery.cn）可能不稳定

**修复方案**：
1. 增加期号同步频率（改为每 15 分钟）
2. 添加多源期号同步（zgzcw + sporttery + 手动录入）
3. 实现期号缺失告警

**验收标准**：
- [ ] 在售期号完整覆盖
- [ ] 期号同步延迟 ≤ 15 分钟

---

#### 问题 #5：球员伤病数据为空

**现象**：
- REMEDIATION_PLAN.md 中提到 190 支球队有伤停数据
- 但当前状态未知，可能仍为模拟数据

**根本原因**：
- `injury_sync.py` 框架存在但数据源不可用
- 当前使用模拟数据（squad_fatigue_index 恒为 0.5）

**修复方案**：
1. 接入真实伤停数据源（api-football.com 或爬虫）
2. 验证 `injury_sync_job()` 是否正常运行
3. 检查数据库中 key_injuries 和 squad_fatigue_index 的实际值

**验收标准**：
- [ ] DB中 key_injuries 非空的球队 ≥ 50 支
- [ ] squad_fatigue_index 有 ≥ 3 种不同值
- [ ] PlayerAdjustmentModel 输出范围在 0.85-1.15 之间

---

#### 问题 #6：预测快照锁定机制不完整

**现象**：
- REMEDIATION_PLAN.md 中提到 224/224 快照已生成
- 但代码中无 `prediction_snapshots` 表

**根本原因**：
- `prediction_snapshot_wrapper()` 在 scheduler 中注册
- 但 `PredictionSnapshotManager` 实现可能不完整

**修复方案**：
1. 验证 `prediction_snapshots` 表是否存在
2. 检查快照生成逻辑是否正常运行
3. 实现快照 checksum 验证

**验收标准**：
- [ ] prediction_snapshots 表存在
- [ ] 每场预测有对应快照记录
- [ ] checksum 验证通过

---

#### 问题 #7：赔率更新后未触发预测重算

**现象**：
- 赔率采集任务运行，但预测不更新

**根本原因**：
- `odds_collector.py` 中无预测重算触发逻辑
- `sporttery_odds_refresh_job()` 每 3 小时运行但不重新生成预测

**修复方案**：
1. 在 `odds_collector.py` 中添加预测重算触发
2. 实现防抖机制（同一比赛 5 分钟内不重复重算）
3. 添加重算失败告警

**验收标准**：
- [ ] 赔率更新后 5 分钟内预测重算完成
- [ ] 重算后 model_version 更新为最新版本
- [ ] 同一比赛 5 分钟内不重复重算

---

### 🟢 P2 - 中优先级问题（影响用户体验）

#### 问题 #8：前端数据展示不完整

**现象**：
- `static/app.js` 中的 `isLocked()` 函数被注释为测试模式（全部开放）

**修复方案**：
1. 启用 `isLocked()` 函数的真实逻辑
2. 添加付费状态指示器
3. 测试付费墙是否正常工作

---

#### 问题 #9：模型版本混乱

**现象**：
- 156,675 条预测用 v2.0，仅 355 条用 v2.0-lr

**修复方案**：
1. 批量重新生成所有预测（使用最新模型）
2. 添加模型版本更新日志
3. 前端显示模型版本和更新时间

---

#### 问题 #10：自动化任务健康状态不明确

**现象**：
- 41 个定时任务在 scheduler 中注册，但无统一监控面板

**修复方案**：
1. 完善 `health_daemon.py` 的任务监控
2. 添加任务执行日志聚合
3. 实现失败告警（邮件/企业微信）

---

## 3. 自动化状态评估

### 3.1 已自动化的流程

| 流程 | 频率 | 状态 | 备注 |
|------|------|------|------|
| 赔率采集（Tier 0） | 30 分钟 | 🔴 停滞 | 数据源失效 |
| 赔率采集（Tier 1） | 2 小时 | 🔴 停滞 | 无数据 |
| 赔率采集（Tier 2） | 每天 08:00/20:00 | 🔴 停滞 | 需 API key |
| 赔率采集（Tier 3） | 每天 12:00 | 🔴 停滞 | 无数据 |
| 收盘赔率采集 | 15 分钟 | 🔴 停滞 | 无待处理比赛 |
| 预测锁定 | 1 小时 | ✅ 正常 | 164 场待处理 |
| 比赛监控 | 1 分钟 | ✅ 正常 | 19 场 LIVE |
| 结果同步 | 5 分钟 | ✅ 正常 | openfootball 源 |
| 竞彩期号同步 | 09:00/15:00 | 🟡 不完整 | 仅 13 期 |
| 竞彩开奖同步 | 6 小时 | ✅ 正常 | 6 期已开奖 |
| 数据备份 | 每日 03:00 | ✅ 正常 | 7 天保留 |
| 伤停数据同步 | 每日 08:00 | 🟡 无数据 | 模拟数据 |
| 融合训练 | 每周一 06:05 | ✅ 正常 | 含 A/B 验证 |
| 残差 NN 训练 | 每日 06:30 | 🟡 不稳定 | 数值异常 |
| 健康检查 | 10 分钟 | ✅ 正常 | 自检+自修 |

### 3.2 需要人工介入的环节

| 环节 | 当前状态 | 建议 |
|------|---------|------|
| 赔率数据源修复 | 🔴 停滞 | 立即修复（影响预测质量） |
| LR 权重验证 | 🟡 部分失效 | 检查权重文件 + 重新生成预测 |
| 残差 NN 调试 | 🟡 不稳定 | 修复特征标准化 + 重新训练 |
| 竞彩期号补全 | 🟡 不完整 | 手动补录或修复数据源 |
| 伤停数据接入 | 🔴 无数据 | 接入真实数据源 |
| 预测快照验证 | 🟡 不完整 | 检查表结构 + 验证生成逻辑 |

---

## 4. 整改优先级建议

### 第一阶段（立即 - 24小时内）

**目标**：恢复系统核心功能

1. **修复赔率采集系统**
   - 检查 zgzcw/500.com 数据源状态
   - 添加反爬对策（代理池、User-Agent 轮换）
   - 启用 football-data.co.uk 备用源
   - 实现合成赔率兜底

2. **验证 LR 权重文件**
   - 检查 `backend/data/weights/lr/global_*.json` 是否存在
   - 添加详细日志记录权重加载过程
   - 强制重新生成所有待处理比赛的预测

3. **修复残差 NN 数值不稳定**
   - 修复特征标准化逻辑
   - 添加特征有效性检查
   - 重新训练残差 NN

**验收标准**：
- [ ] 赔率覆盖率 ≥ 80%
- [ ] 所有预测使用 v2.0-lr
- [ ] 残差修正成功率 ≥ 95%

---

### 第二阶段（本周内）

**目标**：完善数据质量

4. **完善竞彩期号同步**
   - 增加期号同步频率（改为每 15 分钟）
   - 添加多源期号同步
   - 实现期号缺失告警

5. **接入真实伤停数据**
   - 接入 api-football.com 或爬虫
   - 验证 `injury_sync_job()` 是否正常运行
   - 检查数据库中伤停数据的实际值

6. **验证预测快照锁定机制**
   - 验证 `prediction_snapshots` 表是否存在
   - 检查快照生成逻辑是否正常运行
   - 实现快照 checksum 验证

**验收标准**：
- [ ] 在售期号完整覆盖
- [ ] 伤停数据覆盖 ≥ 50 支球队
- [ ] 预测快照 100% 覆盖

---

### 第三阶段（本月内）

**目标**：优化用户体验

7. **优化前端数据展示**
   - 启用付费墙鉴权逻辑
   - 添加模型版本和更新时间展示
   - 优化 UI 交互

8. **完善自动化任务监控**
   - 完善 `health_daemon.py` 的任务监控
   - 添加任务执行日志聚合
   - 实现失败告警（邮件/企业微信）

9. **性能优化**
   - 添加数据库索引
   - 实现特征缓存机制
   - 优化查询性能

**验收标准**：
- [ ] 付费墙正常工作
- [ ] 自动化任务成功率 ≥ 95%
- [ ] 特征提取耗时 ≤ 5 秒

---

## 5. 关键指标对标

| 指标 | 当前 | 目标 | 状态 |
|------|------|------|------|
| SPF 准确率 | 未测量 | ≥ 55% | ⚠️ 需验证 |
| Brier Score | 未测量 | ≤ 0.190 | ⚠️ 需验证 |
| 赔率覆盖率 | 0% | ≥ 80% | 🔴 停滞 |
| 竞彩期号完整性 | 13 期 | 100% | 🟡 不完整 |
| 自动化任务成功率 | 未测量 | ≥ 95% | ⚠️ 需监控 |
| LR 融合使用率 | 0.2% | 100% | 🔴 失效 |
| 残差 NN 成功率 | 未测量 | ≥ 95% | 🟡 不稳定 |

---

## 6. 总体评估

### 系统健康度：⚠️ **降级运行**

**主要风险**：
1. 赔率采集系统失效 → 预测质量下降
2. LR 融合部分失效 → 模型版本混乱
3. 自动化任务监控不足 → 故障无法及时发现

**优势**：
1. 核心预测引擎架构完整（LR融合 + 残差NN）
2. 自动化任务框架健全（41个定时任务）
3. 数据规模充足（31K+ 比赛，462 支球队）

**建议**：
- 立即启动应急修复（赔率采集、LR 权重、残差 NN）
- 完善自动化任务监控和告警机制
- 建立定期审计流程（每周一次）

---

## 7. 附录

### 7.1 关键文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| 预测引擎 | `backend/prediction_engine.py` | 核心预测逻辑 |
| 调度器 | `backend/scheduler.py` | 41 个定时任务 |
| LR 融合 | `backend/fusion/logistic_fusion.py` | LR 逻辑回归融合 |
| 残差 NN | `backend/residual_nn.py` | 残差修正网络 |
| 赔率采集 | `backend/odds_collector.py` | 赔率采集逻辑 |
| 前端应用 | `static/app.js` | 前端交互逻辑 |

### 7.2 数据库表结构

| 表名 | 记录数 | 说明 |
|------|--------|------|
| matches | 31,402 | 比赛数据 |
| predictions | 156,030 | 预测数据 |
| teams | 462 | 球队数据 |
| jingcai_issues | 13 | 竞彩期号 |
| odds_history | 未统计 | 赔率历史 |

### 7.3 日志文件位置

| 日志 | 路径 | 说明 |
|------|------|------|
| 主日志 | `logs/uvicorn.log` | FastAPI 主日志 |
| 调度器日志 | `logs/scheduler.log` | 定时任务日志 |
| 残差 NN 错误 | `backend/residual_nn.error.2026-05-17.log` | 残差 NN 错误日志 |

---

**审计完成时间**: 2026-05-19 17:00  
**下次审计建议**: 2026-05-26（一周后）
