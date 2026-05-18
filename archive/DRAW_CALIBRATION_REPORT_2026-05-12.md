# 今日改动报告：依赖补齐、平局检测/校准、时间滚动验证

生成日期：2026-05-12  
项目路径：`/Users/liuxuran/Github/football`

## 1. 改动范围

本次围绕三个目标完成：

1. 补齐 `requirements.txt` 中缺失的运行依赖。
2. 实现可复用的平局检测/校准模块，并接入预测与验证流程。
3. 新增时间滚动验证脚本，重新生成一版平局校准指标。

涉及文件：

| 文件 | 类型 | 说明 |
|---|---|---|
| `backend/requirements.txt` | 修改 | 新增 `slowapi==0.1.9` 与 `torch>=2.2.0` |
| `backend/draw_calibrator.py` | 新增 | 平局信号、概率校准、参数网格、指标评估 |
| `backend/walk_forward_draw_validation.py` | 新增 | 时间滚动验证脚本，输出指标和推荐参数 |
| `backend/validate_predictions.py` | 修改 | 增加 `--draw-calibrated` 验证开关 |
| `backend/prediction_engine.py` | 修改 | 预测引擎改为读取统一平局校准参数 |
| `backend/data/draw_calibration/params.json` | 新增/生成 | 当前推荐平局校准参数 |
| `backend/data/draw_calibration/walk_forward_metrics.json` | 新增/生成 | 时间滚动验证完整指标 |

## 2. 依赖补齐

已在 `backend/requirements.txt` 中新增：

```txt
slowapi==0.1.9
torch>=2.2.0
```

说明：

- `slowapi` 是 `backend/main.py` 中限流中间件的必需依赖。
- `torch` 是 `bet_nn.py`、`sub_model_score.py`、`sub_model_handicap.py`、`sub_model_halftime.py` 的必需依赖。
- 当前本地 `backend/venv` 尚未安装这两个包，因此测试仍会在导入 `main.py` 时因 `ModuleNotFoundError: No module named 'slowapi'` 失败。需要执行 `pip install -r backend/requirements.txt` 后再跑完整测试。

## 3. 平局检测/校准实现

新增 `backend/draw_calibrator.py`，核心能力：

- `DrawCalibrationParams`：平局校准参数结构。
- `DrawFeatures`：单场平局判断特征，包括 Elo 差、xG 差、市场平局概率、是否淘汰赛。
- `apply_draw_calibration()`：在多个平局信号同时满足时提升 draw 概率。
- `tune_params()`：在训练窗口上选择校准参数。
- `evaluate_rows()`：计算 accuracy、Brier、Log Loss、平局预测率。

当前预测引擎已接入统一参数读取：

```python
return apply_draw_calibration(spf, features, load_draw_params())
```

实际生效参数来自：

```txt
backend/data/draw_calibration/params.json
```

## 4. 时间滚动验证结果

执行命令：

```bash
cd backend
venv/bin/python walk_forward_draw_validation.py --min-train 3000 --train-window 6000 --test-window 2000 --step 2000
```

验证配置：

| 项 | 值 |
|---|---:|
| 历史样本 | 30,644 场 |
| 有效测试样本 | 27,644 场 |
| fold 数 | 14 |
| 最小训练窗 | 3,000 场 |
| 训练窗口 | 6,000 场 |
| 测试窗口 | 2,000 场 |
| 步长 | 2,000 场 |

结果：

| 指标 | 原始模型 | 平局校准后 |
|---|---:|---:|
| 方向准确率 | 47.34% | 47.10% |
| Brier Score | 0.2091 | 0.2095 |
| Log Loss | 1.0477 | 1.0500 |
| 平局预测率 | 0.00% | 6.77% |
| 实际平局率 | 25.06% | 25.06% |

结论：

- 平局校准确实让模型开始预测平局，平局预测率从 `0.00%` 提升到 `6.77%`。
- 但 walk-forward 结果显示，方向准确率、Brier、Log Loss 均变差。
- 因此本次校准未被接受，系统自动保存为禁用参数。

当前 `params.json`：

```json
{
  "enabled": false,
  "elo_diff_threshold": 120.0,
  "market_draw_threshold": 0.26,
  "model_draw_threshold": 0.24,
  "xg_diff_threshold": 0.5,
  "min_signals": 2,
  "min_draw_prob": 0.18,
  "draw_boost": 1.3,
  "draw_cap": 0.45,
  "promote_draw": false,
  "promote_min_signals": 3,
  "promote_margin": 0.005
}
```

## 5. 当前验证结果

执行：

```bash
cd backend
venv/bin/python validate_predictions.py --draw-calibrated
```

由于当前平局校准参数为 `enabled=false`，验证结果与原始模型一致：

| 玩法 | 准确率 | Brier |
|---|---:|---:|
| SPF 胜平负 | 46.7% | 0.2104 |
| RQ 让球胜平负 | 49.0% | 0.2128 |
| Score 比分 | 11.1% | - |
| Goals 总进球 | 23.3% | - |
| Half 半全场 | 31.8% | - |

## 6. 测试状态

已通过语法检查：

```bash
backend/venv/bin/python -m py_compile \
  backend/draw_calibrator.py \
  backend/walk_forward_draw_validation.py \
  backend/validate_predictions.py \
  backend/prediction_engine.py
```

冒烟测试仍失败：

```bash
cd backend
venv/bin/python -m pytest tests -q
```

失败原因：

```txt
ModuleNotFoundError: No module named 'slowapi'
```

这是环境未安装新依赖导致，不是代码语法错误。安装依赖后需要重新运行。

## 7. 关键判断

本次最重要的结论是：简单规则型平局 boost 不是有效改进。

虽然项目确实存在“模型从不预测平局”的严重问题，但直接后处理提升 draw 概率会误伤更多主胜/客胜命中场次。当前应保持校准禁用，下一步应改为真正的平局二分类/多任务模型，而不是规则 boost。

建议下一步：

1. 安装依赖并恢复测试环境：`pip install -r backend/requirements.txt`。
2. 训练独立 `draw_detection` 二分类模型，目标是 `actual_outcome == "draw"`。
3. 使用时间滚动验证选择阈值，不用随机切分。
4. 将平局模型作为 gating：只有当 `P(draw)` 超过验证阈值时才允许 draw 成为 top1。
5. 继续以 Brier/Log Loss 作为接受条件，避免只追求方向准确率。
