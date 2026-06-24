# SPF 预测准确率提升诊断报告

**日期**: 2026-06-24  
**当前状态**: 准确率 53.9%, Brier=0.191  
**目标**: 准确率 ≥ 55%, Brier ≤ 0.18  

---

## 一、数据事实（数据层）

### 1.1 数据库现状

| 指标 | 数值 |
|------|------|
| 总比赛数 | 32 (4 FINISHED + 28 SCHEDULED) |
| 已结束比赛 | 4 (全部 International Friendly) |
| v2.0 SPF 预测数 | 4 |
| v2.0 方向准确率 | 50.0% (2/4) |
| v2.0 Brier Score | 0.2454 |
| v3.0_classic SPF 预测数 | 4 |
| v3.0_classic 方向准确率 | 75.0% (3/4) |

### 1.2 训练数据规模（真实）

| 来源 | 样本数 | 说明 |
|------|--------|------|
| Fusion LR 训练集 | **31,228** 场比赛 | 来自 PostgreSQL 远程库 |
| Draw Classifier 训练集 | **31,402** 场比赛 | 平局率 24.89% |
| Halftime 子模型 | **23,576** 场比赛 | |
| Score 子模型 | **31,353** 场比赛 | |
| Handicap 子模型 | **22,188** 场比赛 | |

### 1.3 本地 SQLite vs 远程 PG 的差异

- **本地 SQLite** (`database.sqlite`): 仅 53 支球队, 32 场比赛, 4 场已结束
- **远程 PostgreSQL**: 31,000+ 场历史比赛用于训练
- **关键问题**: 本地数据库几乎无法用于任何有意义的准确率验证

### 1.4 球队数据质量

| 字段 | 填充情况 |
|------|----------|
| avg_xg / avg_xga | **全部为 None** (53/53 支球队未填充) |
| rest_days | 全部为默认值 7 |
| key_injuries | 全部为空字符串 |
| recent_results | 全部为空字符串 |
| form_factor | 范围 0.92~1.10，差异极小 |
| possession / pass_completion | 全部为 None |

---

## 二、问题根因分析

### 2.1 【CRITICAL】LR 逻辑回归权重退化

**发现**: 最新的全局 LR 权重 (`global_v1_2026-06-15.json`) 出现了严重退化:

| 指标 | 旧版本 (May 15) | 最新版本 (Jun 15) | 变化 |
|------|-----------------|-------------------|------|
| 样本数 | 5,000 | 31,228 | +524% |
| 准确率 | **56.56%** | **48.03%** | **-8.5%** |
| Cross Entropy | 0.913 | **1.321** | **+45%** |
| L1 Penalty | 0.001 | **0.0** | 正则化失效 |
| Intercept Home | 未记录 | -0.158 | |
| Intercept Away | 未记录 | **0.500** | 严重偏向客胜 |

**根因分析**:
1. **样本污染**: 从 5,000 样本扩展到 31,228 样本时，引入了大量低质量数据（友谊赛、弱队比赛、无赔率比赛）
2. **L1 正则化为 0**: `l1_penalty: 0.0` 意味着没有任何特征选择，模型过拟合噪声
3. **Baseline 偏移**: intercept_away=0.5 意味着全零特征时 P(away)=47.1%, P(draw)=28.6%, P(home)=24.4%——完全反向
4. **特征维度不匹配**: weights 有 53 维但 `logistic_fusion.py` 的 `FEATURE_NAMES` 只有 45 个名称，`explain()` 会索引越界

**影响**: LR Fusion 路径实际上在**降低**预测质量，而非提升。

### 2.2 【HIGH】v3.0_classic 优于 v2.0

对比 4 场已知结果的预测:

| 比赛 | 实际结果 | v2.0 预测 | v3.0_classic 预测 |
|------|----------|-----------|-------------------|
| 阿根廷 vs 意大利 | Home | Home (0.85) | Home (0.72) |
| 巴西 vs 德国 | **Draw** | Home (0.81) ❌ | Home (0.46) |
| 葡萄牙 vs 比利时 | Home | Home (0.81) | Home (0.62) |
| 美国 vs 墨西哥 | **Away** | Home (0.72) ❌ | **Away (0.43)** ✓ |

**关键洞察**: v3.0_classic 虽然也错了 1 场（巴西vs德国），但它正确预测了美国vs墨西哥的客胜。v2.0 连续 4 场全部预测主胜，严重缺乏区分度。

**原因**: v2.0 使用了退化的 LR 权重 + 50% 市场赔率融合，导致概率被压缩到主胜一侧。v3.0_classic 绕过了 LR Fusion，直接使用 EnsembleFusion，保留了更多模型多样性。

### 2.3 【HIGH】特征工程缺陷

#### 2.3.1 avg_xg/avg_xga 从未被使用

```python
# features/feature_builder.py 中没有使用 avg_xg/avg_xga
# 这些字段在 Team 表中有定义，但全部为 None
```

Poisson 模型的 `_compute_lambdas()` 使用 `avg_xg` 作为首选，fallback 到 `avg_goals_scored`。由于 avg_xg 全部为 None，**整个 xG 信号丢失**。

#### 2.3.2 rest_days 全部使用默认值

```python
# features/feature_builder.py 第159行
rest_h = getattr(ctx.home_team, "rest_days", 5)  # 默认 5
rest_a = getattr(ctx.away_team, "rest_days", 5)
```

所有球队 rest_days=7（数据库默认值），rest_days_sync.py 从未被执行或执行失败。这意味着 **rest_advantage 特征永远为 0**。

#### 2.3.3 is_derby 硬编码为 0

```python
# features/feature_builder.py 第173行
0.0, # is_derby  # 永远是 0
```

LR 权重中 `is_derby` 的 importance 排名第四（imp=3.36），但实际训练中这个特征永远为 0，导致模型学到的是噪声。

#### 2.3.4 8 个特征权重为零

`global_v1_2026-06-15.json` 中以下特征在 home 和 away 系数中都为零:
- `lambda_home`（泊松主期望进球）
- `home_rest` / `away_rest`（休息天数）
- `is_late_season`（赛季末）
- `I_elo_form`（交互特征）
- `market_win`（市场主胜概率）
- `form_win` / `momentum`（状态特征）

**这意味着这些特征完全没有参与预测。**

### 2.4 【MEDIUM】Draw Detection 效果有限

| 指标 | 数值 |
|------|------|
| 训练样本 | 31,402 |
| 平局率 | 24.89% |
| 验证集分离度 | 8.6% |
| 平局召回率 | 60.6% |
| 最大 draw_boost | 0.05 (5%) |

**问题**:
1. 验证分离度仅 8.6%，说明 NN 分类器区分平局/非平局的能力很弱
2. `max_boost=0.05` 意味着即使 NN 高度确信是平局，最多也只增加 5% 的平局概率
3. `threshold=0.65` 过高，大部分情况下不会触发 draw boost

### 2.5 【MEDIUM】NN 修正层可能适得其反

```python
# prediction_nn_correction.py 第51行
final_spf = {k: 0.4 * spf[k] + 0.6 * stacking_spf[k] ...}  # 60% NN
# prediction_nn_correction.py 第102行
fused_spf = {k: 0.5 * fused_spf[k] + 0.5 * bet_nn_spf[k] ...}  # 50% BetNN
```

两层 NN 修正叠加（StackingNet 60% + BetNN 50%），但:
- Score 子模型准确率仅 6.3%（几乎随机）
- Halftime 子模型准确率仅 43.5%
- Handicap 子模型准确率仅 42.6%

NN 修正层的训练数据质量存疑，可能引入额外噪声。

### 2.6 【LOW】Elo 模型平局公式过于简单

```python
# core/models/elo.py 第26行
draw_base = 0.25 + 0.10 * math.exp(-abs(diff) / 200.0)
```

当 Elo 差值为 0 时，平局概率仅 25.9%。这个公式没有考虑:
- 联赛特性（意甲平局率 ~28%，英超 ~25%）
- 比赛阶段（淘汰赛平局率更高）
- 战术风格（防守型对阵产生更多平局）

---

## 三、改进方案（按预期提升排序）

### 方案 1: 回滚并重训 LR 权重（预期 +0.8~1.2%）

**文件**: `fusion/logistic_fusion.py`, `fusion/fusion_trainer.py`

**具体操作**:
1. 弃用 `global_v1_2026-06-15.json`（48% 准确率），回滚到 `global_v1_2026-05-25.json`（57.4% 准确率）
2. 恢复 L1 正则化: `l1_penalty=0.001`（当前为 0）
3. 修复特征维度不一致: `logistic_fusion.py` 的 `FEATURE_NAMES` 从 45 补到 53（添加 `elo_drift`, `relative_goals`, `market_volatility`, `ref_severity`, `ref_home_bias`, `home_rest`, `away_rest`, `is_late_season`, `pressure_index`, `is_prime_time`）
4. 在 `_build()` 中加入数据质量过滤:
   ```python
   # 过滤掉 avg_xg 全部为 None 的球队数据
   # 过滤掉友谊赛（比赛性质不稳定）
   # 确保 closing_odds 在 kickoff 之前采集
   ```
5. 使用 walk-forward 时间序列交叉验证，而非随机 CV

**代码修改**:
- `fusion/fusion_trainer.py` 的 `_build()` 方法中添加数据质量门控
- `fusion/logistic_fusion.py` 的 `cross_validate_lambda()` 默认恢复 `l1_penalty=0.001`
- `core/prediction_engine.py` 的 `_load_lr_weights()` 添加准确率阈值检查，低于 50% 时 fallback 到 EnsembleFusion

### 方案 2: 修复 avg_xg/avg_xga 数据管道（预期 +0.5~0.8%）

**文件**: `ingestion/feature_sync.py`, `features/feature_builder.py`, `core/models/poisson.py`

**具体操作**:
1. 确认 `sync_features.py` 是否能正确填充 `avg_xg`/`avg_xga`:
   ```bash
   cd backend && python sync_features.py --dry-run  # 检查填充率
   ```
2. 如果 xG 数据源不可用，用 `avg_goals_scored`/`avg_goals_conceded` 的滚动均值替代:
   ```python
   # features/feature_builder.py 中新增
   home_xg = ctx.home_team.avg_xg or ctx.home_team.avg_goals_scored
   ```
3. 在 Poisson 模型的 `_compute_lambdas()` 中增加 xG 加权:
   ```python
   # 用近 5 场 xG 加权平均，而非赛季总量
   recent_xg_weight = min(1.0, recent_matches / 5.0)
   effective_xg = recent_xg_weight * avg_xg + (1 - recent_xg_weight) * season_xg
   ```

### 方案 3: 启用 rest_days 真实计算（预期 +0.3~0.5%）

**文件**: `ingestion/rest_days_sync.py`, `features/feature_builder.py`

**具体操作**:
1. 执行 rest_days 同步:
   ```bash
   cd backend && python ingestion/rest_days_sync.py
   ```
2. 修复 `rest_days_sync.py` 的逻辑: 当前使用**中位数间隔**而非**距上一场的天数**，应改为:
   ```python
   # 计算距今天数（或开球日前一天）
   last_match = max(dates)  # 最近的比赛
   rest = (target_date - last_match).days
   ```
3. 在 `feature_builder.py` 中使用真实 rest_days:
   ```python
   # 当前: rest_h = getattr(ctx.home_team, "rest_days", 5)
   # 修复: 确保 ctx 中传递的是真实值
   ```

### 方案 4: 改进 Draw Detection 策略（预期 +0.3~0.5%）

**文件**: `core/models/draw_detection.py`, `core/draw_classifier.py`

**具体操作**:
1. 降低 `DrawClassifierPredictor` 的 threshold:
   ```python
   # 当前: self.threshold = 0.65
   # 改为: self.threshold = 0.45
   ```
2. 提高 `max_boost`:
   ```python
   # 当前: self.max_boost = 0.05
   # 改为: self.max_boost = 0.10
   ```
3. 增加联赛特定的平局先验:
   ```python
   LEAGUE_DRAW_PRIOR = {
       "SerieA": 0.28, "LaLiga": 0.26, "EPL": 0.25,
       "Bundesliga": 0.24, "Ligue1": 0.27,
   }
   ```
4. 当 Elo 差值 < 50 且赔率对称时，强制提升平局概率 3%

### 方案 5: 简化 NN 修正层或改用轻量级校准（预期 +0.2~0.4%）

**文件**: `core/prediction_nn_correction.py`, `core/residual_nn.py`, `core/bet_nn.py`

**具体操作**:
1. 暂时禁用 StackingNet 修正（Score 子模型 6.3% 准确率表明训练数据有问题）:
   ```python
   # prediction_nn_correction.py
   # 将 0.4 * spf + 0.6 * stacking 改为 0.9 * spf + 0.1 * stacking
   ```
2. 保留 BetNN 但降低权重:
   ```python
   # 将 0.5 * fused + 0.5 * betnn 改为 0.8 * fused + 0.2 * betnn
   ```
3. 或者完全移除两层 NN，改用**概率校准 (Platt Scaling)**:
   ```python
   # 对 LR Fusion 输出的概率做 Platt Scaling 校准
   # 比 NN 修正更稳定，不易过拟合
   ```

---

## 四、实施优先级与预期总提升

| 优先级 | 方案 | 预期提升 | 难度 | 风险 |
|--------|------|----------|------|------|
| P0 | 回滚+重训 LR 权重 | +0.8~1.2% | 中 | 低 |
| P1 | 修复 avg_xg 数据管道 | +0.5~0.8% | 中 | 低 |
| P2 | 启用 rest_days 真实计算 | +0.3~0.5% | 低 | 低 |
| P3 | 改进 Draw Detection | +0.3~0.5% | 低 | 中 |
| P4 | 简化 NN 修正层 | +0.2~0.4% | 低 | 低 |

**预期总提升: +2.1~3.4%**，即从 53.9% → **56.0~57.3%**，超过 55% 目标。

---

## 五、立即可执行的验证步骤

```bash
# 1. 检查 xG 数据填充率
cd backend && python3 -c "
import sqlite3; c = sqlite3.connect('database.sqlite').cursor()
c.execute('SELECT COUNT(*) FROM teams WHERE avg_xg IS NOT NULL')
print(f'Teams with avg_xg: {c.fetchone()[0]}')
c.execute('SELECT COUNT(*) FROM teams WHERE rest_days != 7')
print(f'Teams with non-default rest_days: {c.fetchone()[0]}')
"

# 2. 切换使用旧版 LR 权重（临时回滚）
cd backend && cp data/weights/lr/global_v1_2026-05-25.json data/weights/lr/global_v1_2026-06-15.json

# 3. 在远程 PG 上执行 walk-forward 验证
# 需要 SSH 到远程服务器执行
```

---

## 六、架构建议（中长期）

1. **LR Fusion 应该作为可插拔模块**: 当 LR 准确率 < 52% 时自动 fallback 到 EnsembleFusion
2. **特征重要性监控**: 每次训练后自动检测零权重特征并告警
3. **数据质量仪表盘**: 实时监控 avg_xg/rest_days/key_injuries 的填充率
4. **简化预测管线**: 当前 6 层变换（Elo→Poisson→LR→NN1→NN2→市场融合）信号衰减严重，建议收敛到 3-4 层
