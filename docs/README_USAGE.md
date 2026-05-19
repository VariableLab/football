# 竞彩预测系统 — 使用文档 v2.0

> **路径**: `/Users/liuxuran/Github/football` | **日期**: 2026-05-16

## 1. 项目概述

基于 **Elo + 泊松(DixonColes) + LR融合 + NN残差修正** 的竞彩足球预测系统。

**四层架构**: 特征生成(43维) → 逻辑回归融合(L1+L-BFGS-B) → NN残差修正(MSE) → 策略管线(4档风险)

**关键指标**: 31,109场比赛 | 竞彩100%覆盖 | LR准确率56.6% | 38个自动化任务

## 2. 目录结构

```
football/
├── README.md / PRD.md / ARCHITECTURE_V2.md / IMPROVEMENT_PLAN.md
├── AUDIT_REPORT_20260516.md / WORK_PLAN.md / README_USAGE.md
│
├── backend/
│   ├── main.py               ★ FastAPI主应用(1910行)
│   ├── models.py             ★ ORM模型(12表)
│   ├── prediction_engine.py  ★ 主预测引擎(1986行)
│   ├── scheduler.py          ★ 调度器(38任务)
│   ├── config.py / auth.py / admin.py / schemas.py
│   │
│   ├── features/             ★ Layer1: 特征生成
│   │   ├── elo_model.py      Elo基线→胜率
│   │   ├── poisson_model.py  泊松+DixonColes(294行)
│   │   ├── market_model.py   赔率去水
│   │   ├── adjustment_models.py 8修正因子(227行)
│   │   ├── form_markov_model.py 马尔可夫时序(299行)
│   │   ├── h2h_model.py      历史交锋
│   │   └── feature_builder.py 43维拼接(220行)
│   │
│   ├── fusion/               ★ Layer2: 融合
│   │   ├── logistic_fusion.py LR+L1+L-BFGS-B(371行)
│   │   └── fusion_trainer.py  DB→训练管线
│   │
│   ├── bet_nn.py / residual_nn.py / draw_classifier.py
│   ├── sub_model_halftime.py / sub_model_score.py / sub_model_handicap.py
│   │
│   ├── sporttery_sync.py     ★ 竞彩同步(主力源)
│   ├── odds_collector.py     多源赔率(1564行)
│   ├── zgzcw_source.py       足彩网(37家)
│   ├── wubaibai_source.py    500.com(20+家)
│   ├── result_sync.py        ★ 结果同步(每5min)
│   ├── auto_learner.py       自动学习触发
│   │
│   ├── strategy_pipeline.py  校准→边际→仓位→风控
│   ├── jingcai_predictor.py  竞彩期号+批量预测(1334行)
│   ├── prediction_report.py  综合报告
│   ├── validation_engine.py  赛后验证
│   ├── weight_learner.py     L-BFGS-B权重学习
│   ├── model_audit.py        漂移检测+自愈
│   ├── data_cleaner.py       6类清洗(687行)
│   ├── health_daemon.py      健康守护
│   │
│   ├── integrations/cloakbrowser_bridge.py
│   ├── backtest_phase1.py / record_jingcai_results.py / seed.py
│   ├── deploy.sh / requirements.txt / .env / .env.example
│   │
│   ├── data/
│   │   ├── football.db       SQLite(31K比赛)
│   │   ├── bet_nn/ / draw_classifier/ / sub_models/
│   │   └── weights/lr/       ★ LR融合权重
│   ├── tests/test_smoke.py
│   └── logs/
│
├── static/
│   ├── index.html / app.js(1208行) / api_client.js
│   └── tailwind.css
└── docs/
```

## 3. 环境配置

### 必需环境变量 (backend/.env)

| 变量 | 说明 | 生成命令 |
|------|------|---------|
| `SECRET_KEY` | JWT签名密钥 | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_API_KEY` | 管理后台Key | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ALLOWED_ORIGINS` | CORS白名单 | 开发: `http://localhost:8000` |
| `DEBUG` | 调试模式 | `true`(开发) / `false`(生产) |

### 可选API Key

| 变量 | 用途 | 注册地址 |
|------|------|---------|
| `ODDS_API_KEY` | 实时赔率 | the-odds-api.com (免费500/月) |
| `FOOTBALL_DATA_API_KEY` | 比赛结果 | football-data.org (免费100/天) |
| `API_FOOTBALL_KEY` | 球员伤病 | api-football.com |
| `STRIPE_SECRET_KEY` | 在线支付 | stripe.com |

> ⚠️ 所有Key实际值在 backend/.env 中, 此文件已在.gitignore。生产环境用环境变量注入。

## 4. 启动

```bash
cd /Users/liuxuran/Github/football/backend
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑填入SECRET_KEY和ADMIN_API_KEY
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# 访问 http://localhost:8000/static/index.html
```

## 5. API 接口

### 公开接口
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/teams` | 球队列表 |
| GET | `/api/matches` | 比赛列表 |
| GET | `/api/jingcai/issues` | 竞彩期号 |
| GET | `/api/jingcai/report` | 每期预测报告 |
| GET | `/api/predictions/{id}/report` | 综合报告 |
| GET | `/api/validation/accuracy` | 模型验证 |

### 需登录(JWT)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/matches/{id}/strategy` | 策略详情(需付费) |
| POST | `/api/sporttery/sync` | 手动同步 |

### 管理后台(X-Api-Key)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/live-odds/start\|stop` | 实时赔率 |
| POST | `/api/bet-nn/train` | 触发NN训练 |
| POST | `/api/jingcai/issues/{id}/results` | 录入开奖 |
| POST | `/api/jingcai/issues/{id}/verify` | 验证预测 |

## 6. 自动化调度

| 频率 | 任务 | 说明 |
|------|------|------|
| 每5分钟 | result_sync | openfootball结果同步 |
| 每15分钟 | auto_learn | 🆕 检测新结果→触发NN训练 |
| 每30分钟 | zgzcw | 足彩网37家欧赔 |
| 每2小时 | 500.com / jingcai_verify | 赔率+竞彩验证 |
| 每日08:00 | sporttery_daily | ⭐ 竞彩同步+预测生成 |
| 每日06:30 | bet_nn / residual_nn | NN训练 |
| 每周一06:05 | fusion_train | 🆕 LR权重学习 |
| 每周一06:15 | self_heal | 自愈闭环 |

## 7. 常用命令

```bash
# 训练LR融合(5000样本,~5分钟)
python3 -c "from fusion.fusion_trainer import FusionTrainer; w=FusionTrainer(limit=5000).train_global(); print(f'acc={w.accuracy:.4f}')"

# 训练残差网络
python3 -c "from residual_nn import residual_nn_train_job; residual_nn_train_job()"

# 回测
python3 backtest_phase1.py

# 录入竞彩结果
python3 record_jingcai_results.py

# 数据快照
python3 -c "from models import SessionLocal,Match,Prediction; s=SessionLocal(); print(f'matches={s.query(Match).count()} preds={s.query(Prediction).count()}'); s.close()"
```

## 8. 数据流

```
竞彩API → sporttery_sync(每日08:00) → Match+Odds+JingcaiIssue
                                        ↓
                              PredictionEngine.predict()
                              ┌─ Elo → Poisson → Market → Player
                              ├─ FormMarkov → H2H
                              ├─ FeatureBuilder(43维)
                              └─ LogisticFusion(56.6%) + ResidualNN
                                        ↓
                              Prediction表(5玩法) → 前端展示

比赛结果 → result_sync(每5分钟) → Match.actual_outcome
                                        ↓
                              auto_learn(每15分钟) → NN增量训练
                              jingcai_verify(每2小时) → 准确率验证
```

## 9. 常见问题

**Q: 准确率为什么只有48-57%?**  
足球随机性极强, 48%远超随机33%, 56%接近专业水平。

**Q: 如何提升准确率?**  
① 运行FusionTrainer().train_global()部署LR融合  
② 接入OddsHarvester+cloakbrowser补充赔率  
③ soccerdata FBref采集球员真实数据

**Q: 竞彩结果没自动更新?**  
sporttery.cn不提供历史结果。系统通过openfootball(五大联赛)+football-data.org获取。

**Q: 数据库在哪?**  
`backend/data/football.db`, 每日03:00自动备份到`backend/backup/`。

> **维护者**: @liuxuran | **相关文档**: PRD.md | ARCHITECTURE_V2.md | AUDIT_REPORT_20260516.md
