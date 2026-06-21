# 模型版本对照表

> 最后更新: 2026-06-21  
> 目的: 消除 README / PRO_OPERATOR_MANUAL / 代码之间的版本叙事不一致

## 实际部署状态

| 版本标签 | 描述 | 实际部署 | 数据来源 |
|---------|------|----------|---------|
| `v2.0` | 4参数线性加权 (Elo 0.35 + Poisson 0.35 + Players 0.05 + Market 0.25) | ✅ 生产实际 | 动态审计: 所有预测 model_version=v2.0 |
| `v2.0-lr` | 48维特征 + L1正则化逻辑回归融合 | ❌ 未部署 | README 宣称但生产未运行 |
| `v3.0` (shadow) | 影子一致性引擎 (基于泊松矩阵+目标SPF拉伸) | ⚠️ 代码存在,部分调用 | `shadow_engine.py` |
| `v3.0_classic` | 纯物理 Dixon-Coles (无拉伸) | ⚠️ 代码存在,部分调用 | `shadow_engine.py` |
| `v4.0` (deep) | 深度学习时序 xG 引擎 | ⚠️ 实验性 | `deep_frontier_nn.py` |
| `PQ-V5.0` | Stacking Residual Intelligence | ❓ 不明 | PRO_OPERATOR_MANUAL, 代码中未找到对应实现 |

## 为什么 LR 融合未部署

根据 `prediction_engine.py:1573-1601` 的逻辑, LR 融合需要 **同时满足**:
1. `weights` 不为 None → 权重文件存在且格式正确 ✅ (global_v1_2026-06-15.json 存在)
2. `real_market` 不为 None → 即 `market_out` 不为 None 且 `is_degraded=False` ❌

**根因**: 大多数比赛的 `closing_odds` 缺失或为 None → `MarketModel.predict()` 返回 None → `real_market = None` → LR 融合被跳过 → fallback 到线性加权。

**解决方案**: 
- 短期: 确保至少 60%+ 比赛有真实 closing_odds (当前 38% synthetic)
- 中期: 修复 LR 融合加载路径,添加诊断日志 (已完成)

## 版本标签规范

所有新预测必须使用以下标签之一:
- `v2.0_linear` — 线性加权 (当前生产)
- `v2.1_lr` — LR 融合 (待部署)
- `v3.0_shadow` — 影子引擎
- `v4.0_deep` — 深度学习

**禁止**: 混用 `v2.0`、`v2.0-lr`、`v3.0` 等不同格式。统一为 `vX.Y_suffix` 格式。
