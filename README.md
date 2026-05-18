# WC Analytics — 竞彩足球预测系统

> 数据驱动的竞彩足球预测平台 · 多层模型融合 · 全自动化管线

---

## 项目概述

基于 **逻辑回归融合 + 物理模型特征 + 神经网络残差修正** 三层架构的足球比赛预测系统。覆盖 31K+ 历史比赛、462 支球队，为每场竞彩在售比赛输出 5 种玩法的概率分布 + EV 价值分析。

**核心原则**：
- ✅ 只展示数学模型计算的概率数字
- ✅ 赛前快照锁定，可追溯验证
- ❌ 不提供任何形式的"投注建议"

---

## 当前状态

| 指标 | 数值 | 说明 |
|------|------|------|
| 比赛总数 | 31,368 | 覆盖 46 个联赛/赛事 |
| 已结束 | 31,120 | 含 230 场世界杯淘汰赛（1930-2022） |
| 球队 | 462 | 自动发现 + 手动录入 |
| SPF 预测 | 155,690 | 5 种玩法全覆盖 |
| 竞彩在售 | 46 场 | 每日 sporttery 同步 |
| 赔率源 | 5 通道 | 竞彩官方 + zgzcw(37家) + 500.com(20+) + BetExplorer |

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vanilla JS + TailwindCSS v4 | 零框架依赖，服务端渲染 |
| **后端** | FastAPI + SQLAlchemy + APScheduler | 41 个定时任务全自动 |
| **预测** | Python + NumPy + SciPy + PyTorch | 3 层架构：LR 融合 + 残差 NN |
| **数据** | SQLite WAL + 自动备份 | 零运维开销 |

---

## 预测架构

```
Layer 1: 特征生成层
  ├── EloModel → 实力基线
  ├── PoissonModel(Dixon-Coles) → 攻防概率矩阵
  ├── MarketModel → 多源赔率去水
  ├── AdjustmentModels → 8 种修正因子
  ├── FormMarkovModel → 时序状态特征
  └── H2HModel → 历史交锋特征

Layer 2: 逻辑回归融合层
  └── LogisticRegression(L1, L-BFGS-B, class_weight)
      → 43 维特征 → SPF 概率输出 (全量 30K+ 训练)

Layer 3: 神经网络残差修正层
  └── ResidualNN (3 层 MLP)
      → 修正 LR 系统性偏差

Layer 4: 策略输出层
  ├── Platt Scaling 概率校准
  ├── EV 边际计算 (模型概率 vs 赔率隐含)
  ├── 4 档风险过滤 (conservative/balanced/aggressive/speculative)
  └── Kelly 仓位优化
```

---

## 性能指标

| 指标 | 旧系统 (线性融合) | 新系统 (LR 融合) | 目标 |
|------|:-:|:-:|:-:|
| SPF 方向准确率 | 48.6% | **56.6%** (回测) | ≥ 55% |
| Brier Score | 0.210 | **~0.185** | ≤ 0.190 |
| 竞彩在售期准确率 | 未测量 | ≥ 55% (预期) | ≥ 55% |

---

## 快速启动

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# 访问 http://localhost:8000/static/index.html
```

---

## 项目结构

```
backend/                     # 后端核心
├── main.py                  # FastAPI 应用 (1910 行)
├── models.py                # ORM 模型 (12 表)
├── prediction_engine.py     # 预测引擎 (3 层融合)
├── scheduler.py             # 41 个定时任务
│
├── features/                # 特征生成层
│   ├── elo_model.py
│   ├── poisson_model.py
│   ├── market_model.py
│   ├── adjustment_models.py
│   ├── form_markov_model.py
│   ├── h2h_model.py
│   └── feature_builder.py   # 43 维特征拼接
│
├── fusion/                  # 融合层
│   ├── logistic_fusion.py   # LR + L1 + class_weight
│   └── fusion_trainer.py    # DB → 特征 → 训练管线
│
├── nn/                      # 神经网络
│   ├── bet_nn.py            # 平局检测器
│   ├── residual_nn.py       # 残差修正网络
│   └── draw_classifier.py   # 平局二分类
│
├── data/                    # 数据存储
│   ├── weights/lr/          # LR 权�%8D 文件
│   ├── bet_nn/              # 神经网络权重
│   ├── draw_classifier/     # 平局分类器权重
│   ├── sub_models/          # 子模型权重
│   └── model_audit/         # 每日复盘报告

docs/                        # 文档
├── AUDIT_REPORT_20260516.md # 全量审计报告 (最详细)
├── ARCHITECTURE_V2.md       # 架构设计 (975 行)
├── IMPROVEMENT_PLAN.md      # 改善计划
└── OPENCLAW_MANUAL.md       # 管理后台手册
```

---

## 关键文档

| 文档 | 说明 |
|------|------|
| [审计报告 2026-05-16](AUDIT_REPORT_20260516.md) | 数据/模型/自动化/安全全面审计 |
| [架构设计 v2](ARCHITECTURE_V2.md) | 三层架构设计原理（必读） |
| [改善计划](IMPROVEMENT_PLAN.md) | 问题诊断 + 修复路线图 |
| [管理操作手册](OPENCLAW_MANUAL.md) | Admin API / 卡密 / 运营操作 |

---

## 近期完善记录

| # | 事项 | 日期 |
|---|------|------|
| 1 | LR 全量重训 (30,887 场, class_weight 平衡平局) | 2026-05-16 |
| 2 | football-data.co.uk 赔率回补 (14,765 行) | 2026-05-16 |
| 3 | 竞彩开奖结果补录 (JC20260509-0514 共 6 期) | 2026-05-16 |
| 4 | 置信度系统修复 (131,390 条预测写入 confidence) | 2026-05-16 |
| 5 | 联赛命名统一清洗 (5,432 行, 44→24 种) | 2026-05-16 |
| 6 | 世界杯淘汰赛历史数据导入 (230 场, 1930-2022) | 2026-05-16 |
| 7 | health_daemon 修复 (自激回路/FN阈值/告警冷却) | 2026-05-16 |

---

## License

仅供学习研究使用。
