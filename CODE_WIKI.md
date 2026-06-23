# WC Analytics - Code Wiki

> **文档版本**: 1.0  
> **最后更新**: 2026-06-22  
> **项目地址**: [https://github.com/VariableLab/football](https://github.com/VariableLab/football)  
> **线上地址**: [https://football.nett.to](https://football.nett.to)

---

## 目录

1. [项目概览](#1-项目概览)
2. [技术架构](#2-技术架构)
3. [目录结构](#3-目录结构)
4. [核心模块详解](#4-核心模块详解)
5. [数据模型](#5-数据模型)
6. [API接口](#6-api接口)
7. [机器学习模型](#7-机器学习模型)
8. [数据采集系统](#8-数据采集系统)
9. [定时任务](#9-定时任务)
10. [前端架构](#10-前端架构)
11. [部署与运维](#11-部署与运维)
12. [依赖关系](#12-依赖关系)
13. [开发指南](#13-开发指南)

---

## 1. 项目概览

### 1.1 项目定位

WC Analytics 是一个**开源的足球比赛概率校准研究框架**，专注于学术研究而非商业投注。项目覆盖 **31,402 场历史比赛** 和 **462 支球队**，构建了 **3 层融合概率建模系统**。

### 1.2 核心特性

- ✅ **概率输出**: 所有输出为数学概率值，赛前锁定可追溯
- ✅ **多层融合**: Elo基线 + 特征模型 + 市场校准
- ✅ **完整验证**: 赛前预测 + 赛后验证闭环
- ✅ **自动化运维**: 自检自愈守护进程
- ✅ **多语言支持**: 6种语言国际化（中/英/法/西/德/意）
- ❌ **非投注建议**: 不构成任何投注建议

### 1.3 项目统计

| 指标 | 数值 | 说明 |
|------|------|------|
| 总比赛数 | 31,402 | 46个联赛/杯赛 |
| 已完成比赛 | 31,238 | 包含230场世界杯淘汰赛(1930-2022) |
| 球队数 | 462 | 自动发现 + 手动录入 |
| 总预测数 | 157,030 | 5种玩法全覆盖 |
| 竞彩期号 | 14 | 自动同步zgzcw |
| 赔率源 | 多渠道 | zgzcw + 历史回填 |

---

## 2. 技术架构

### 2.1 技术栈总览

| 层级 | 技术 | 用途 |
|------|------|------|
| **后端框架** | FastAPI (Python 3.10+) | REST API + SSE + 静态文件服务 |
| **数据库** | SQLite (WAL模式) / PostgreSQL | 单文件数据库，支持高并发 |
| **ORM** | SQLAlchemy 2.0 | 数据模型与查询 |
| **任务调度** | APScheduler | 定时采集/刷新/自愈 |
| **前端** | 原生 HTML/CSS/JS | SPA风格，Tailwind CSS |
| **代理** | Nginx | HTTPS终止 + API反向代理 |
| **系统服务** | systemd | 进程管理 + 自重启 |
| **机器学习** | PyTorch + scipy | 神经网络 + 概率校准 |

### 2.2 架构设计图

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│    Nginx     │────▶│   FastAPI    │
│  (SPA)       │◀────│  (HTTPS)     │◀────│  Uvicorn     │
│              │     │              │     │   :8000      │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                    ┌─────────────────────────────┤
                    │              │              │
               ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
               │ SQLite  │   │ APSched │   │PyTorch  │
               │  WAL    │   │ 定时器  │   │ 模型    │
               └─────────┘   └─────────┘   └─────────┘
                    │
                    │  外部数据源
                    ├── zgzcw.com (中国足彩网百家欧赔)
                    ├── 500.com (百家欧赔)
                    ├── football-data.org (赛果/积分)
                    ├── the-odds-api.com (国际赔率)
                    └── deepstock.zone.id (AI分析)
```

### 2.3 设计决策

| 决策 | 理由 |
|------|------|
| SQLite (非PostgreSQL) | 单服务器部署，无高并发写入需求，运维零成本 |
| 原生JS (非React/Vue) | 页面功能聚焦、无复杂状态管理，减少构建步骤 |
| i18n内嵌中文翻译 | 避免XHR异步加载导致首屏翻译缺失 |
| 单worker | live-odds SSE用模块级全局变量，多worker需Redis |
| Nginx反代 + Let's Encrypt | 轻量级HTTPS终止，管理简便 |

---

## 3. 目录结构

### 3.1 根目录结构

```
football/
├── backend/                    # Python后端核心
├── static/                     # 前端静态文件
├── docs/                       # 项目文档
├── research/                   # 研究脚本与实验
├── tests/                      # 测试代码
├── pitch_deck/                 # 产品演示幻灯片
├── promo-video/                # 宣传视频(Remotion)
├── screenshots/                # 产品截图
├── data/                       # 数据权重
├── README.md                   # 项目说明
├── README_ZH.md                # 中文说明
├── package.json                # 前端构建配置
└── pytest.ini                  # 测试配置
```

### 3.2 Backend目录详解

```
backend/
├── main.py                     # FastAPI入口 (2077行)
├── database/
│   ├── models.py               # ORM模型 (16个表)
│   └ config.py                 # 配置管理
│   └── __init__.py
│
├── api/                        # API路由层
│   ├── routers/
│   │   ├── matches.py          # 比赛相关API
│   │   ├── jingcai.py          # 竞彩期号API
│   │   ├── auth.py             # 用户认证API
│   │   ├── validation.py       # 模型验证API
│   │   ├── strategy.py         # 策略分析API
│   │   ├── live.py             # 滚球赔率API
│   │   ├── health.py           # 健康检查API
│   │   ├── advisor.py          # AI分析API
│   │   ├── feedback.py         # 用户反馈API
│   │   ├── monitor.py          # 监控API
│   │   ├── settings.py         # 用户设置API
│   │   ├── models.py           # 模型管理API
│   │   ├── events.py           # 事件推送API
│   │   ├── content.py          # 内容生成API
│   │   ├── admin_management.py # 管理员API
│   │   ├── license.py          # 卡密兑换API
│   │   └── public.py           # 公开数据API
│   ├── admin.py                # 管理路由
│   ├── auth.py                 # 认证模块
│   ├── schemas.py              # Pydantic模型
│   └ compat_routes.py          # 兼容路由
│   └── __init__.py
│
├── core/                       # 核心预测引擎
│   ├── prediction_engine.py    # 3层融合引擎
│   ├── calibrator.py           # 概率校准
│   ├── bet_nn.py               # Bet神经网络
│   ├── residual_nn.py          # 残差网络
│   ├── deep_frontier_nn.py     # 深度前沿网络
│   ├── draw_calibrator.py      # 平局校准
│   ├── draw_classifier.py      # 平局分类器
│   ├── hedge_engine.py         # 套利引擎
│   ├── live_hedge_engine.py    # 滚球对冲
│   ├── shadow_engine.py        # 影子引擎
│   ├── prediction_report.py    # 预测报告
│   ├── prediction_snapshot.py  # 预测快照
│   ├── prediction_recalc.py    # 预测重算
│   ├── context.py              # 数据结构
│   ├── constants.py            # 常量定义
│   ├── factory.py              # 工厂模式
│   ├── base.py                 # 基类
│   ├── agent_engine.py         # Agent引擎
│   ├── agent_brain.py          # Agent大脑
│   ├── agent_tools.py          # Agent工具
│   ├── license_manager.py      # 卡密管理
│   ├── logic_tracer.py         # 逻辑追踪
│   ├── model_registry.py       # 模型注册
│   ├── research_elo.py         # Elo研究
│   ├── research_poisson.py     # Poisson研究
│   └── models/                 # 子模型
│       ├── elo.py              # Elo模型
│       ├── poisson.py          # Poisson模型
│       ├── heavy_tail_poisson.py # 重尾泊松
│       ├── market.py           # 市场模型
│       ├── form_adjustment.py  # 状态修正
│       ├── home_away.py        # 主客场因素
│       ├── tactical.py         # 战术因素
│       ├── weather_venue.py    # 天气场地
│       ├── schedule_density.py # 赛程密度
│       ├── squad_availability.py # 阵容可用性
│       ├── player_adjustment.py # 球员修正
│       ├── coach_impact.py     # 教练影响
│       ├── draw_detection.py   # 平局检测
│       ├── upset_detector.py   # 冷门检测
│       ├── outlier_detector.py # 异常检测
│       └ mixture_score_model.py # 混合比分模型
│       └── __init__.py
│
├── features/                   # 特征工程层
│   ├── feature_builder.py      # 特征拼接器(48维)
│   ├── elo_model.py            # Elo特征
│   ├── poisson_model.py        # Poisson特征
│   ├── market_model.py         # 市场特征
│   ├── form_markov_model.py    # 状态马尔可夫
│   ├── h2h_model.py            # 历史对战
│   ├── adjustment_models.py    # 修正因子
│   └── __init__.py
│
├── fusion/                     # 融合层
│   ├── logistic_fusion.py      # LR融合(主力)
│   ├── ensemble_fusion.py      # 线性加权融合
│   ├── fusion_trainer.py       # 融合训练器
│   ├── validate_deploy.py      # 验证部署
│   └── __init__.py
│
├── strategy/                   # 策略层
│   ├── strategy_pipeline.py    # 策略管线
│   ├── edge_calculator.py      # 边际计算
│   ├── position_sizer.py       # 仓位计算
│   ├── risk_manager.py         # 风控管理
│   ├── odds_ev_finder.py       # EV查找器
│   ├── optimal_combo.py        # 最优组合
│   ├── hedged_portfolio.py     # 对冲组合
│   └── __init__.py
│
├── ingestion/                  # 数据采集层
│   ├── zgzcw_source.py         # 中国足彩网采集
│   ├── wubaibai_source.py      # 500.com采集
│   ├── odds_collector.py       # 赔率采集器
│   ├── odds_tracker.py         # 赔率追踪
│   ├── live_odds_feed.py       # 滚球赔率
│   ├── result_sync.py          # 赛果同步
│   ├── injury_sync.py          # 伤病数据
│   ├── form_collector.py       # 状态采集
│   ├── data_cleaner.py         # 数据清洗
│   ├── data_quality_gate.py    # 数据质量门
│   ├── sporttery_sync.py       # sporttery同步
│   ├── jingcai_quant_collector.py # 竞彩量化采集
│   ├── openfootball_importer.py # OpenFootball导入
│   ├── backfill_team_stats.py  # 球队统计回填
│   ├── bf_volume_scraper.py    # BF成交量爬虫
│   ├── sync_jc_to_server.py    # 本地→服务器同步
│   ├── sync_real_fixtures.py   # 真实赛程同步
│   └── data/
│       └── team_aliases.yaml   # 队名映射
│
├── integrations/               # 第三方集成
│   ├── soccerdata_adapter.py   # SoccerData适配
│   ├── oddsharvester_bridge.py # OddsHarvester桥接
│   ├── cloakbrowser_bridge.py  # CloakBrowser桥接
│   └── _cloak_scripts/
│       └ fetch_pages.mjs       # 页面抓取脚本
│
├── monitor/                    # 监控与运维
│   ├── scheduler.py            # 定时任务中心(1423行)
│   ├── health_daemon.py        # 自检自愈守护
│   ├── alert_manager.py        # 告警管理
│   ├── model_audit.py          # 模型审计
│   ├── validation_engine.py    # 验证引擎
│   ├── validation_framework.py # 验证框架
│   ├── strategy_monitor.py     # 策略漂移监控
│   ├── diagnostic_backtest.py  # 诊断回测
│   ├── auto_learner.py         # 自动学习器
│   ├── shadow_audit.py         # 影子审计
│   └ exchange_anomaly_daemon.py # 交易所异常守护
│   └ data/
│       ├── alerts.json         # 告警记录
│       └ health_status.json    # 健康状态
│
├── scripts/                    # 脚本工具
│   ├── tools/
│   │   ├── prediction_recalc.py # 预测重算
│   │   ├── regenerate_predictions.py # 重新生成预测
│   │   ├── backfill_confidence.py # 回填置信度
│   │   ├── fill_remaining_confidence.py # 填充剩余置信度
│   │   └ save_learned_weights.py # 保存学习权重
│   │   └ check_data_for_dataset.py # 数据检查
│   ├── maintenance/
│   │   ├── emergency_fix.py    # 紧急修复
│   │   ├── fill_jingcai_results.py # 填充竞彩结果
│   │   ├── record_jingcai_results.py # 记录竞彩结果
│   │   └ refresh_odds.py       # 刷新赔率
│   ├── legacy/
│   │   ├── backtest_2018_wc.py # 2018世界杯回测
│   │   ├── backtest_2022_wc.py # 2022世界杯回测
│   │   ├── import_worldcup.py  # 导入世界杯
│   ├── auto_generate_previews.py # 自动生成预览
│   ├── auto_team_stats.py      # 自动球队统计
│   ├── backup_database.py      # 数据库备份
│   ├── cleanup_logs.py         # 清理日志
│   ├── collect_data.py         # 数据采集
│   ├── daily_ai_report.py      # 每日AI报告
│   ├── fix_data_depth.py       # 修复数据深度
│   ├── form_collector.py       # 状态采集
│   ├── full_historical_backtest.py # 全历史回测
│   ├── fusion_strategy.py      # 融合策略
│   ├── health_check.py         # 健康检查
│   ├── import_manual_form.py   # 导入手动状态
│   ├── league_accuracy_test.py # 联赛准确率测试
│   ├── monitor_live_accuracy.py # 监控实时准确率
│   ├── news_morale_agent.py    # 新闻士气Agent
│   ├── odds_api_fetch.py       # Odds API抓取
│   ├── param_optimizer.py      # 参数优化
│   ├── profit_training.py      # 盈利训练
│   ├── regenerate_predictions.py # 重新生成预测
│   ├── retrain_all.py          # 全量重训
│   ├── run_lr_fast.py          # 快速LR训练
│   ├── run_train.py            # 训练脚本
│   ├── save_learned_weights.py # 保存权重
│   ├── server_refresh.py       # 服务器刷新
│   ├── strategy_config.py      # 策略配置
│   ├── stress_test.py          # 压力测试
│   ├── sub_model_halftime.py   # 半全场子模型
│   ├── sub_model_handicap.py   # 让球子模型
│   ├── sub_model_score.py      # 比分子模型
│   ├── sync_wc_completed_results.py # 同步世界杯结果
│   ├── tiered_strategy.py      # 分层策略
│   ├── train_league_models.py  # 联赛模型训练
│   ├── validate_predictions.py # 验证预测
│   ├── verify_prediction.py    # 验证单个预测
│   ├── verify_v4.py            # 验证v4
│   ├── weight_learner.py       # 权重学习
│   ├── xg_estimator.py         # xG估算
│   └── __init__.py
│
├── tests/                      # 测试代码
│   ├── test_prediction_engine.py
│   ├── test_feature_builder.py
│   ├── test_strategy_pipeline.py
│   ├── test_health_daemon.py
│   ├── test_logistic_fusion.py
│   ├── test_bet_nn.py
│   ├── test_shadow_engine.py
│   ├── test_scheduler_jobs.py
│   ├── test_model_audit.py
│   ├── test_data_cleaner.py
│   ├── test_news_morale_agent.py
│   ├── test_content_engine.py
│   ├── test_morale_leakage_mitigation.py
│   ├── test_leakage_mitigation.py
│   ├── test_temporal_safety.py
│   ├── test_exchange_monitor.py
│   ├── test_mixture_model.py
│   ├── test_lr_trainer.py
│   ├── test_deep_frontier.py
│   ├── test_strategy_kelly_integration.py
│   ├── test_backup_database.py
│   ├── test_cleanup_logs.py
│   ├── test_smoke.py
│   ├── conftest.py
│   └── __init__.py
│
├── utils/                      # 工具库
│   ├── logger.py               # 日志系统
│   ├── cache.py                # 缓存工具
│   ├── observability.py        # 可观测性
│   ├── sse.py                  # SSE推送
│   ├── telegram_notifier.py    # Telegram通知
│   ├── openclaw.py             # OpenClaw工具
│   └── __init__.py
│
├── data/                       # 数据存储
│   ├── weights/                # 模型权重
│   │   ├── lr/                 # LR权重
│   │   ├── nn/                 # NN权重
│   │   └ research/             # 研究权重
│   ├── bet_nn/                 # BetNN数据
│   ├── draw_classifier/        # 平局分类器
│   ├── draw_calibration/       # 平局校准
│   ├── strategy/               # 策略数据
│   ├── sub_models/             # 子模型数据
│   ├── session_logs/           # 会话日志
│   ├── football.db             # SQLite数据库
│   ├── wc_analytics.db         # WC分析数据库
│   ├── model_config.yaml       # 模型配置
│   ├── team_aliases.yaml       # 队名别名
│   ├── alerts.json             # 告警记录
│   ├── health_status.json      # 健康状态
│   └── jingcai_matches.csv     # 竞彩比赛
│
├── requirements.txt            # Python依赖
├── .env.example                # 环境变量模板
├── .gitignore                  # Git忽略
├── deploy.sh                   # 部署脚本
├── start.sh                    # 启动脚本
├── gunicorn.conf.py            # Gunicorn配置
├── telegram_bot.py             # Telegram机器人
├── create_pg_tables.py         # PG表创建
├── migrate_to_pg.py            # PG迁移
├── test_import.py              # 导入测试
├── test_pg.py                  # PG测试
├── manual_form.json            # 手动状态
├── AUDIT_REPORT_2026-06-22.md  # 审计报告
├── DEPLOY.md                   # 部署文档
└ └ run_lr_full.sh              # LR全量训练
│
└── main.py                     # FastAPI入口
```

---

## 4. 核心模块详解

### 4.1 预测引擎 (PredictionEngine)

**文件**: [backend/core/prediction_engine.py](file:///Users/liuxuran/Github/football/backend/core/prediction_engine.py)

**职责**: 3层融合概率预测的核心引擎

**架构**:
```
输入特征 → Layer1(Elo/Poisson/Market) → Layer2(LR融合) → Layer3(校准) → 输出概率
```

**关键类**:

#### `PredictionEngine`

```python
class PredictionEngine:
    """
    世界杯预测引擎
    
    包含:
      - Elo实力模型
      - 泊松攻防模型(双变量)
      - 球员状态修正
      - 市场赔率隐含概率
      - 线性融合层
      - 回测框架
    """
    
    def predict(self, match: Match, context: MatchContext) -> PredictionResult:
        """
        主预测入口
        
        Args:
            match: 比赛对象
            context: 比赛上下文(球队状态、历史等)
            
        Returns:
            PredictionResult: 包含全部6种玩法的概率分布
        """
```

**预测流程**:
1. **Elo基线**: 计算两队Elo评分差异 → 基础胜平负概率
2. **Poisson攻防**: 计算主客队期望进球数 → Dixon-Coles修正
3. **球员修正**: 伤病/轮换/士气 → 调整期望进球
4. **市场融合**: 多源赔率去vig → 市场隐含概率
5. **LR融合**: 48维特征 → 多项式逻辑回归 → SPF概率
6. **校准**: 分段线性校准 → 修正概率偏差
7. **输出**: 生成6种玩法(SPF/RQ/比分/总进球/半全场)概率

---

### 4.2 特征构建器 (FeatureBuilder)

**文件**: [backend/features/feature_builder.py](file:///Users/liuxuran/Github/football/backend/features/feature_builder.py)

**职责**: 将所有子模型输出拼接为48维特征向量

**特征清单** (48维):

| 类别 | 维度 | 特征 |
|------|------|------|
| **A. Elo** | 8维 | elo_diff, elo_win, elo_draw, elo_away, is_heavy_fav, is_heavy_udog, elo_tier_diff, elo_drift |
| **B. Poisson** | 8维 | lambda_home, lambda_away, lambda_diff, poisson_win, poisson_draw, poisson_away, goal_exp, relative_goals |
| **C. Players** | 4维 | home_avail, away_avail, avail_diff, injury_impact |
| **D. Market** | 7维 | market_win, market_draw, market_away, overround, max_odds_move, source_count, market_volatility |
| **E. Form** | 5维 | form_win, form_draw, momentum, stability, streak_norm |
| **F. H2H** | 6维 | h2h_total_norm, h2h_win, h2h_draw, h2h_recent, h2h_goals_norm, first_meeting |
| **G. Meta** | 10维 | rest_advantage, is_knockout, is_derby, ref_severity, ref_home_bias, home_rest, away_rest, is_late_season, pressure_index, is_prime_time |

**关键类**:

```python
class FeatureBuilder:
    """
    高精度特征拼接器
    集成Karpathy的第一性原理: 减少冗余特征,强化核心博弈信号
    """
    
    def build(
        self,
        elo_probs: Dict[str, float],
        poisson_result: Dict,
        players_factor: float,
        market_probs: Optional[Dict[str, float]],
        form_features,
        h2h_features,
        ctx,
    ) -> np.ndarray:
        """
        拼接48维高精度特征向量
        
        Returns:
            np.ndarray: shape (48,)
        """
```

---

### 4.3 逻辑回归融合 (LogisticFusion)

**文件**: [backend/fusion/logistic_fusion.py](file:///Users/liuxuran/Github/football/backend/fusion/logistic_fusion.py)

**职责**: 多项式逻辑回归融合，替代旧的线性加权

**数学模型**:
```
log(P_home / P_draw) = beta_home · X
log(P_away / P_draw) = beta_away · X
P = softmax([logodds_h, 0, logodds_a])
```

**关键类**:

```python
@dataclass
class LogisticFusionWeights:
    """
    逻辑回归融合权重
    
    Attributes:
        coef_home: (D,) 主胜log-odds系数
        coef_away: (D,) 客胜log-odds系数
        intercept_home: 主胜截距
        intercept_away: 客胜截距
        l1_penalty: L1正则化系数
        input_dim: 输入维度(48)
        league: 联赛标识
        trained_at: 训练时间
        sample_count: 样本数
        cross_entropy: 交叉熵损失
        accuracy: 准确率
    """
    
    def predict(self, features: np.ndarray) -> Dict[str, float]:
        """
        推理: 特征 → 概率
        
        Args:
            features: shape (D,) 或 (N, D)
            
        Returns:
            {"home": float, "draw": float, "away": float}
        """
```

**特性**:
- ✅ 自然输出校准概率
- ✅ L1正则化自动特征选择
- ✅ 系数即特征贡献，完全可解释
- ✅ 支持联赛分层训练
- ✅ 兼容scipy.optimize.minimize

---

### 4.4 概率校准器 (Calibrator)

**文件**: [backend/core/calibrator.py](file:///Users/liuxuran/Github/football/backend/core/calibrator.py)

**职责**: 修正模型概率偏差，使输出更接近真实命中率

**校准方法**: 分段线性校准(Piecewise Linear Calibration)

**原理**:
```
将模型概率分到若干桶(bin)
每桶计算实际命中率 vs 模型平均概率
用校准因子修正: calibrated = model_p × factor
```

**关键类**:

```python
class Calibrator:
    """
    概率校准器
    
    独立Poisson模型在低概率区过度自信(模型说12%,实际5%)
    高概率区则欠自信(模型说70%,实际72%)
    校准器用历史数据修正这一偏差
    """
    
    def calibrate_spf(
        self, 
        probs: Dict[str, float]
    ) -> Dict[str, float]:
        """
        校准胜平负概率
        
        Args:
            probs: {"home": 0.58, "draw": 0.24, "away": 0.18}
            
        Returns:
            校准后的概率字典
        """
```

**默认校准曲线** (从30K五大联赛walk-forward数据拟合):

| 模型概率范围 | 校准因子 | 说明 |
|--------------|----------|------|
| 0.00-0.10 | 0.70 | 极低概率: 模型严重过度自信 |
| 0.10-0.15 | 0.42 | 低概率: 过度自信最严重 |
| 0.15-0.20 | 0.67 | |
| 0.20-0.25 | 0.77 | |
| 0.25-0.30 | 0.90 | |
| 0.30-0.35 | 0.90 | |
| 0.35-0.40 | 0.86 | |
| 0.40-0.45 | 0.99 | 交叉点: 模型开始准确 |
| 0.45-0.50 | 0.97 | |
| 0.50-0.55 | 1.04 | 高概率: 模型欠自信 |
| 0.55-0.60 | 0.99 | |
| 0.60-0.65 | 1.01 | |
| 0.65-0.70 | 1.06 | |
| 0.70-0.75 | 1.04 | |
| 0.75-0.80 | 1.04 | |
| 0.80-0.85 | 1.09 | |
| 0.85-1.00 | 1.10 | |

---

### 4.5 策略管线 (StrategyPipeline)

**文件**: [backend/strategy/strategy_pipeline.py](file:///Users/liuxuran/Github/football/backend/strategy/strategy_pipeline.py)

**职责**: 校准→边际→过滤→仓位→风控→输出的完整策略管线

**管线流程**:
```
原始概率 → 校准修正 → 边际计算 → 过滤 → 仓位优化 → 风控检查 → 输出
```

**四级风险档位**:

| 档位 | 校准概率≥ | 赔率范围 | 边际≥ | Kelly系数 |
|------|-----------|----------|-------|-----------|
| conservative (稳健) | 50% | 1.6-2.5 | 3% | 1/8 Kelly |
| balanced (均衡) | 40% | ≤3.5 | 3% | 1/4 Kelly |
| aggressive (进取) | 35% | 任意 | 0% | 1/4 Kelly |
| speculative (激进) | 25% | 任意 | 0% | 1/2 Kelly |

**关键类**:

```python
class StrategyPipeline:
    """
    最优策略管线
    
    替代旧的5策略并行模型估算
    使用一条管线 × 四个风险档位
    """
    
    def generate(
        self,
        predictions: Dict,
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        risk_tier: RiskTier = RiskTier.BALANCED,
    ) -> List[StrategyPick]:
        """
        生成策略建议
        
        Args:
            predictions: 预测概率字典
            odds_home/draw/away: 赔率
            risk_tier: 风险档位
            
        Returns:
            List[StrategyPick]: 策略建议列表
        """
```

---

### 4.6 Bet神经网络 (BetNN)

**文件**: [backend/core/bet_nn.py](file:///Users/liuxuran/Github/football/backend/core/bet_nn.py)

**职责**: 独立预测学习系统，闭环训练

**架构**:
```
BetNet: 3层MLP (input→64→32→16→output)
输入: 模型预测(SPF3+RQ3+比分top3) + 赔率(3) + Elo差(1) + 赔率变动(3) + 联赛类型(4) = 20维
输出: 每个选项(home/draw/away)的预测评分(0-1)
训练标签: 实际结果one-hot，用加权BCE损失
```

**关键类**:

```python
class BetNet(nn.Module):
    """
    3层MLP神经网络
    
    Architecture:
        input(20) → fc1(64) → relu → fc2(32) → relu → fc3(16) → relu → output(3)
    """
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        前向传播
        
        Args:
            x: (batch, 20)
            
        Returns:
            (batch, 3) - home/draw/away评分
        """
```

**闭环训练流程**:
```
比赛结果录入 → 构建训练集 → 增量训练 → 更新策略建议
```

---

### 4.7 健康守护进程 (HealthDaemon)

**文件**: [backend/monitor/health_daemon.py](file:///Users/liuxuran/Github/football/backend/monitor/health_daemon.py)

**职责**: 自检 + 自修引擎，每10分钟运行一次

**检查项目**:

| 检查项 | 频率 | 自修动作 |
|--------|------|----------|
| DB完整性 | 10分钟 | SQLite integrity_check |
| 赔率新鲜度 | 10分钟 | 触发采集任务 |
| 调度器任务 | 10分钟 | 重启失败任务 |
| 数据完整性 | 10分钟 | 回填缺失数据 |
| zgzcw同步 | 10分钟 | 触发同步 |
| 竞彩期号 | 10分钟 | 自动关闭过期期号 |
| 数据流 | 10分钟 | 检查采集→预测→验证流程 |
| 模型漂移 | 10分钟 | 触发重训 |
| 连续失败 | 10分钟 | 告警通知 |
| 备份新鲜度 | 10分钟 | 触发备份 |

**关键类**:

```python
class HealthDaemon:
    """
    自检 + 自修引擎
    """
    
    def run_all_checks(self) -> HealthReport:
        """
        运行所有检查
        
        Returns:
            HealthReport: 包含所有检查结果的报告
        """
    
    def _check_db_integrity(self) -> None:
        """检查数据库完整性"""
    
    def _check_odds_freshness(self) -> None:
        """检查赔率新鲜度"""
    
    def _check_model_drift(self) -> None:
        """检查模型漂移"""
```

---

### 4.8 调度器 (Scheduler)

**文件**: [backend/monitor/scheduler.py](file:///Users/liuxuran/Github/football/backend/monitor/scheduler.py)

**职责**: 定时任务中心，所有自动化任务的注册与执行

**任务列表**:

| 任务 | 间隔 | 功能 |
|------|------|------|
| `collect_zgzcw_job` | 30分钟 | 采集中国足彩网百家欧赔 |
| `collect_500_job` | 30分钟 | 采集500.com百家欧赔 |
| `collect_odds_tier1_job` | 2小时 | Tier1基础赔率更新 |
| `collect_odds_tier1_secondary_job` | 2小时 | 二级赔率源更新 |
| `refresh_odds_job` | 1小时 | 综合赔率刷新 |
| `predict_upcoming_job` | 1小时 | 为即将开赛比赛生成预测 |
| `self_heal_job` | 2小时 | 健康自检 + 自愈 |
| `model_audit_job` | 6小时 | 模型审计 |
| `train_bet_nn_job` | 12小时 | BetNN自动训练 |
| `train_sub_models_job` | 12小时 | 子模型自动训练 |
| `drift_check_job` | 6小时 | 策略漂移检测 |
| `param_optimize_job` | 24小时 | 参数寻优 |
| `validation_job` | 6小时 | 验证数据更新 |
| `scrape_jingcai_job` | 2小时 | 竞彩数据采集 |
| `collect_form_job` | 6小时 | 球队近期状态采集 |
| `sync_results_job` | 6小时 | 赛果同步 |
| `auto_close_issues_job` | 1小时 | 自动关闭过期期号 |
| `sporttery_sync_job` | 6小时 | sporttery同步 |

**关键函数**:

```python
def start_scheduler():
    """
    启动调度器
    注册所有定时任务
    """

def stop_scheduler():
    """
    停止调度器
    """
```

---

### 4.9 赔率采集源 (ZgzcwSource)

**文件**: [backend/ingestion/zgzcw_source.py](file:///Users/liuxuran/Github/football/backend/ingestion/zgzcw_source.py)

**职责**: 从中国足彩网(zgzcw.com)采集百家欧赔

**采集策略**:
1. 从 live.zgzcw.com 获取当日比赛列表
2. 解析结构化赔率数据:
   - `div.oupei > span × 3` → 欧赔(home/draw/away)
   - `div.yapan > span × 3` → 亚盘
   - `div.jcsp > span × 3` → 竞彩SPF
   - `div.jcrqsp > span × 3` → 竞彩让球SP
3. 欧赔作为主赔率，各家详情存入multi_pool_odds

**特点**:
- ✅ 纯HTML解析，无需JS/headless浏览器
- ✅ 一场请求同时获取欧赔+亚盘+竞彩SP
- ✅ 免费、无API key、无请求次数限制
- ✅ 覆盖37家博彩公司

**关键类**:

```python
class ZgzcwOddsSource(OddsSource):
    """
    中国足彩网(zgzcw.com)赔率采集
    """
    
    def _fetch_match_index(self, force: bool = False) -> Dict[int, Dict]:
        """
        从live.zgzcw.com获取当日比赛列表
        
        Returns:
            Dict[int, Dict]: match_id → 比赛信息+赔率
        """
    
    def collect_zgzcw_odds(self, db: Session) -> Dict:
        """
        采集百家欧赔
        
        Returns:
            {"updated": int, "matches": int}
        """
```

---

## 5. 数据模型

### 5.1 核心表结构

**文件**: [backend/database/models.py](file:///Users/liuxuran/Github/football/backend/database/models.py)

#### 核心表清单

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `matches` | 比赛主表 | home_team_id, away_team_id, kickoff_at, odds_home/draw/away, status, match_type, actual_outcome |
| `teams` | 球队 | name, flag, fifa_rank, elo, form_factor |
| `predictions` | 预测记录 | match_id, play_type, probabilities(JSON), model_version, locked_at |
| `users` | 用户 | email, password_hash, is_paid, paid_until |
| `jingcai_issues` | 竞彩期号 | issue_id, issue_type, status(on_sale/drawn/verified), sale_start/end |
| `jingcai_issue_matches` | 期号↔比赛关联 | issue_id, match_id, sequence, handicap, rq_odds/score_odds(JSON) |
| `odds_history` | 赔率历史快照 | match_id, odds_home/draw/away, source, recorded_at |
| `match_bookmaker_odds` | 多博彩公司赔率 | match_id, bookmaker, odds_home/draw/away, updated_at |
| `feedback` | 用户留言 | user_id, category, content, likes |
| `license_keys` | 卡密 | key, license_type, is_used, used_by |
| `user_settings` | 用户偏好 | risk_tier, default_play_type, show_ev |
| `license_redemptions` | 卡密兑换记录 | user_id, license_id, redeemed_at |

#### 关键模型定义

```python
class Match(Base):
    """
    比赛主表
    
    Attributes:
        id: 主键
        home_team_id: 主队ID
        away_team_id: 客队ID
        kickoff_at: 开赛时间
        odds_home/draw/away: 欧赔
        status: 比赛状态(scheduled/upcoming/live/finished/postponed)
        match_type: 比赛类型(world_cup/friendly/warm_up/qualifier)
        actual_outcome: 实际结果(home/draw/away)
        actual_goals: 实际比分(home_goals:away_goals)
    """
    __tablename__ = "matches"
    
    id = Column(Integer, primary_key=True, index=True)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    kickoff_at = Column(DateTime(timezone=True), nullable=False, index=True)
    odds_home = Column(Float, nullable=True)
    odds_draw = Column(Float, nullable=True)
    odds_away = Column(Float, nullable=True)
    status = Column(Enum(MatchStatus), default=MatchStatus.SCHEDULED, index=True)
    match_type = Column(Enum(MatchType), default=MatchType.WORLD_CUP)
    actual_outcome = Column(String(10), nullable=True)
    actual_goals = Column(String(10), nullable=True)
```

```python
class Prediction(Base):
    """
    预测记录
    
    Attributes:
        match_id: 比赛ID
        play_type: 玩法类型(SPF/RQ/SCORE/GOALS/HALF)
        probabilities: 概率分布(JSON)
        model_version: 模型版本
        locked_at: 锁定时间(赛前)
        confidence: 置信度
    """
    __tablename__ = "predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    play_type = Column(Enum(PlayType), nullable=False)
    probabilities = Column(JSON, nullable=False)
    model_version = Column(String(20), default="v2.0")
    locked_at = Column(DateTime(timezone=True), nullable=True)
    confidence = Column(Float, default=0.0)
```

```python
class JingcaiIssue(Base):
    """
    竞彩期号
    
    Attributes:
        issue_id: 期号ID(如JC20260520)
        issue_type: 期号类型(jingcai)
        status: 状态(on_sale/drawn/verified)
        sale_start/end: 销售开始/结束时间
        draw_at: 开奖时间
        total_matches: 总比赛数
    """
    __tablename__ = "jingcai_issues"
    
    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(String(20), unique=True, index=True, nullable=False)
    issue_type = Column(String(20), default="jingcai")
    status = Column(String(20), default="on_sale", index=True)
    sale_start = Column(DateTime(timezone=True), nullable=True)
    sale_end = Column(DateTime(timezone=True), nullable=True)
    draw_at = Column(DateTime(timezone=True), nullable=True)
    total_matches = Column(Integer, default=0)
```

### 5.2 数据流图

```
[sporttery.cn / zgzcw.com] ──采集──▶ [matches + jingcai_issue_matches]
                                            │
                                      [prediction_engine]
                                            │
                                      [predictions 表]
                                            │
                    ┌───────────────────────┤
                    │                       │
              [strategy_pipeline]    [strategy_pipeline]
              (Kelly 仓位)           (AI 分析/串关)
                    │                       │
              [strategy_picks]        [AI 分析报告]
```

---

## 6. API接口

### 6.1 API路由总览

**文件**: [backend/main.py](file:///Users/liuxuran/Github/football/backend/main.py) + [backend/api/routers/](file:///Users/liuxuran/Github/football/backend/api/routers/)

| 路由前缀 | 文件 | 功能 |
|----------|------|------|
| `/api/auth/*` | api/routers/auth.py | 用户注册/登录/获取信息 |
| `/api/matches/*` | api/routers/matches.py | 比赛列表/详情/策略/赔率变动 |
| `/api/jingcai/*` | api/routers/jingcai.py | 竞彩期号CRUD/预测/开奖/验证/报告 |
| `/api/validation/*` | api/routers/validation.py | 模型验证/校准曲线/玩法准确率 |
| `/api/feedback/*` | api/routers/feedback.py | 留言CRUD/点赞 |
| `/api/live-odds/*` | api/routers/live.py | 滚球赔率SSE/轮询/启动/停止 |
| `/api/live-hedge/*` | api/routers/live.py | 滚球对冲/仓位/计算 |
| `/api/arbitrage` | api/routers/matches.py | 跨博彩公司套利扫描 |
| `/api/health` | api/routers/health.py | 健康检查 + 详细报告 |
| `/api/settings/*` | api/routers/settings.py | 用户偏好设置 |
| `/api/bet-nn/*` | api/routers/models.py | 预测神经网络状态/推理/训练 |
| `/api/sub-models/*` | api/routers/models.py | 子模型状态/训练 |
| `/api/predictions/*` | api/routers/models.py | 综合预测报告 |
| `/api/strategy/*` | api/routers/strategy.py | 策略参数/分层分析/寻优/漂移监控 |
| `/api/sporttery/*` | api/routers/public.py | sporttery.cn数据同步 |
| `/api/chat` | api/routers/advisor.py | AI分析(调用OpenAI兼容API) |
| `/api/admin/*` | api/routers/admin_management.py | 赔率刷新/数据质量审计/清洗 |
| `/api/license/*` | api/routers/license.py | 卡密兑换 |
| `/api/monitor/*` | api/routers/monitor.py | 监控数据 |
| `/api/events/*` | api/routers/events.py | 事件推送 |
| `/api/content/*` | api/routers/content.py | 内容生成 |

### 6.2 关键API接口详解

#### 比赛相关API

```python
# GET /api/matches
# 获取比赛列表
def get_matches(
    status: Optional[str] = None,
    match_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    获取比赛列表
    
    Query Params:
        status: 比赛状态(scheduled/upcoming/live/finished)
        match_type: 比赛类型(world_cup/friendly/qualifier)
        limit: 返回数量
        offset: 偏移量
    
    Returns:
        List[MatchResponse]
    """

# GET /api/matches/{match_id}
# 获取比赛详情
def get_match_detail(
    match_id: int,
    db: Session = Depends(get_db),
):
    """
    获取比赛详情
    
    Returns:
        MatchDetailResponse: 包含预测、赔率历史、策略建议
    """

# GET /api/matches/{match_id}/strategy
# 获取策略建议
def get_match_strategy(
    match_id: int,
    risk_tier: str = "balanced",
    bankroll: float = 1000,
    db: Session = Depends(get_db),
):
    """
    获取策略建议
    
    Query Params:
        risk_tier: 风险档位(conservative/balanced/aggressive/speculative)
        bankroll: 本金
    
    Returns:
        StrategyResponse: 包含Kelly仓位、EV、风险评估
    """
```

#### 竞彩期号API

```python
# POST /api/jingcai/issues
# 创建竞彩期号
def create_jingcai_issue(
    issue_data: JingcaiIssueCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    创建竞彩期号
    
    Body:
        {
            "issue_id": "JC20260520",
            "issue_type": "jingcai",
            "sale_start": "2026-05-20T08:00:00Z",
            "sale_end": "2026-05-21T08:00:00Z",
            "match_codes": ["001", "002", ...]
        }
    
    Returns:
        JingcaiIssueResponse
    """

# POST /api/jingcai/issues/{issue_id}/predict
# 为整期比赛生成预测
def predict_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    为整期比赛生成预测
    
    Returns:
        {"predicted": int, "total": int}
    """

# POST /api/jingcai/issues/{issue_id}/results
# 录入开奖结果
def record_jingcai_results(
    issue_id: str,
    results_data: JingcaiResultsInput,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    录入开奖结果
    
    Body:
        {
            "results": [
                {"match_code": "001", "spf_result": "3", "rq_result": "0", ...}
            ],
            "prizes": {"spf_3": 1500, ...},
            "draw_at": "2026-05-21T10:00:00Z"
        }
    
    Returns:
        {"recorded": int}
    """

# POST /api/jingcai/issues/{issue_id}/verify
# 验证模型预测 vs 开奖结果
def verify_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    验证模型预测
    
    Returns:
        VerificationReport: 包含命中率、校准曲线、错误分析
    """
```

#### 模型验证API

```python
# GET /api/validation/accuracy
# 获取模型准确率
def get_validation_accuracy(
    play_type: str = "SPF",
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    获取模型准确率
    
    Query Params:
        play_type: 玩法类型
        days: 统计天数
    
    Returns:
        {
            "direction_accuracy": float,
            "brier_score": float,
            "calibration_curve": [...]
        }
    """

# GET /api/validation/calibration
# 获取校准曲线
def get_calibration_curve(
    play_type: str = "SPF",
    db: Session = Depends(get_db),
):
    """
    获取校准曲线
    
    Returns:
        CalibrationCurveResponse
    """
```

#### AI分析API

```python
# POST /api/chat
# AI自然语言分析
def ai_chat(
    chat_request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional),
):
    """
    AI自然语言分析
    
    Body:
        {
            "match_id": 123,
            "question": "这场比赛怎么看?",
            "context": "optional context"
        }
    
    Returns:
        {
            "response": "AI分析文本",
            "match_summary": {...}
        }
    """
```

---

## 7. 机器学习模型

### 7.1 模型架构总览

```
┌─────────────────────────────────────┐
│  Layer 1: 特征生成层                 │
│  ├── EloModel → Elo基线             │
│  ├── PoissonModel → 攻防矩阵        │
│  ├── MarketModel → 市场隐含概率     │
│  ├── FormMarkovModel → 状态特征     │
│  ├── H2HModel → 历史对战特征        │
│  └── AdjustmentModels → 8修正因子   │
└─────────────────────────────────────┘
              ↓ 48维特征向量
┌─────────────────────────────────────┐
│  Layer 2: 逻辑回归融合层             │
│  LogisticRegression(L1, L-BFGS-B)   │
│  → 43特征 → SPF概率                  │
│  (30K+比赛训练)                      │
└─────────────────────────────────────┘
              ↓ 融合概率
┌─────────────────────────────────────┐
│  Layer 3: 残差神经网络层             │
│  ResidualNN (3-layer MLP)           │
│  → 修正LR系统性偏差                  │
└─────────────────────────────────────┘
              ↓ 校准概率
┌─────────────────────────────────────┐
│  Layer 4: 策略输出层                 │
│  ├── Platt Scaling校准              │
│  ├── EV计算(模型概率 vs 赔率隐含)   │
│  ├── 4档风险过滤                     │
│  └── Kelly仓位优化                   │
└─────────────────────────────────────┘
```

### 7.2 子模型详解

#### Elo模型

**文件**: [backend/features/elo_model.py](file:///Users/liuxuran/Github/football/backend/features/elo_model.py)

**原理**: 基于Elo评分系统计算基础胜平负概率

```python
class EloModel:
    """
    Elo实力模型
    
    公式:
        P(home) = 1 / (1 + 10^((elo_away - elo_home - HOME_ADVANTAGE) / 400))
        P(draw) ≈ DRAW_INFLATION_FACTOR × 基础平局概率
        P(away) = 1 - P(home) - P(draw)
    """
    
    def predict(
        self, 
        elo_home: int, 
        elo_away: int, 
        is_neutral: bool = False
    ) -> Dict[str, float]:
        """
        计算Elo基线概率
        
        Args:
            elo_home: 主队Elo评分
            elo_away: 客队Elo评分
            is_neutral: 是否中立场地
            
        Returns:
            {"home": float, "draw": float, "away": float}
        """
```

#### Poisson模型

**文件**: [backend/features/poisson_model.py](file:///Users/liuxuran/Github/football/backend/features/poisson_model.py)

**原理**: Dixon-Coles双变量泊松模型，计算主客队期望进球数

```python
class PoissonModel:
    """
    泊松攻防模型(Dixon-Coles修正)
    
    公式:
        lambda_home = attack_home × defense_away × home_advantage
        lambda_away = attack_away × defense_home
        
        P(score_h, score_a) = Poisson(lambda_home, score_h) × Poisson(lambda_away, score_a) × Dixon-Coles修正
    """
    
    def predict(
        self,
        attack_home: float,
        defense_home: float,
        attack_away: float,
        defense_away: float,
        home_advantage: float = 1.2,
    ) -> Dict:
        """
        计算泊松攻防概率
        
        Returns:
            {
                "lambda_home": float,
                "lambda_away": float,
                "score_matrix": np.ndarray,  # (MAX_GOALS+1, MAX_GOALS+1)
                "spf_probs": {"home": float, "draw": float, "away": float}
            }
        """
```

#### 市场模型

**文件**: [backend/features/market_model.py](file:///Users/liuxuran/Github/football/backend/features/market_model.py)

**原理**: 多源赔率去vig，计算市场隐含概率

```python
class MarketModel:
    """
    市场赔率模型
    
    去vig方法:
        1. 计算overround = 1/odds_home + 1/odds_draw + 1/odds_away - 1
        2. 市场隐含概率 = 1/odds / (1 + overround)
        
    多源融合:
        - zgzcw百家欧赔(37家)
        - 500.com百家欧赔(20+家)
        - the-odds-api国际赔率
    """
    
    def predict(
        self,
        odds_home: float,
        odds_draw: float,
        odds_away: float,
    ) -> Dict[str, float]:
        """
        计算市场隐含概率
        
        Returns:
            {"home": float, "draw": float, "away": float, "overround": float}
        """
```

#### 状态马尔可夫模型

**文件**: [backend/features/form_markov_model.py](file:///Users/liuxuran/Github/football/backend/features/form_markov_model.py)

**原理**: 基于近期比赛结果的马尔可夫链状态特征

```python
class FormMarkovModel:
    """
    状态马尔可夫模型
    
    特征:
        - 近10场胜负序列 → 转移概率矩阵
        - momentum: 近5场 momentum指数
        - stability: 状态稳定性(方差)
        - streak_norm: 连胜/连败归一化
    """
    
    def extract_features(
        self,
        recent_matches: List[Dict],
    ) -> Dict[str, float]:
        """
        提取状态特征
        
        Args:
            recent_matches: 近10场比赛结果列表
            
        Returns:
            {"form_win": float, "form_draw": float, "momentum": float, ...}
        """
```

#### 历史对战模型

**文件**: [backend/features/h2h_model.py](file:///Users/liuxuran/Github/football/backend/features/h2h_model.py)

**原理**: 历史对战记录特征

```python
class H2HModel:
    """
    历史对战模型
    
    特征:
        - h2h_total_norm: 总对战次数归一化
        - h2h_win/draw/away: 历史胜平负比例
        - h2h_recent: 近3次对战结果
        - h2h_goals_norm: 历史进球数归一化
        - first_meeting: 是否首次交锋
    """
    
    def extract_features(
        self,
        h2h_matches: List[Dict],
    ) -> Dict[str, float]:
        """
        提取历史对战特征
        
        Returns:
            {"h2h_total_norm": float, "h2h_win": float, ...}
        """
```

### 7.3 神经网络模型

#### BetNN (预测神经网络)

**文件**: [backend/core/bet_nn.py](file:///Users/liuxuran/Github/football/backend/core/bet_nn.py)

**架构**:
```python
class BetNet(nn.Module):
    """
    3层MLP
    
    Layer1: input(20) → fc1(64) → relu → dropout(0.2)
    Layer2: 64 → fc2(32) → relu → dropout(0.2)
    Layer3: 32 → fc3(16) → relu
    Output: 16 → output(3) → sigmoid
    
    输入特征(20维):
        - SPF概率(3): home/draw/away
        - RQ概率(3): home/draw/away
        - 比分top3概率(3)
        - 赔率隐含概率(3)
        - Elo差归一化(1)
        - 赔率变动(3)
        - 联赛类型one-hot(4)
    
    输出(3维):
        - home/draw/away评分(0-1)
    """
```

**训练流程**:
```
比赛结果录入 → 构建训练集(特征+标签) → 增量训练 → 更新权重 → 验证准确率
```

#### ResidualNN (残差网络)

**文件**: [backend/core/residual_nn.py](file:///Users/liuxuran/Github/football/backend/core/residual_nn.py)

**职责**: 修正LR融合的系统性偏差

```python
class ResidualNet(nn.Module):
    """
    残差网络
    
    用于修正LR融合输出的系统性偏差
    输入: LR融合概率 + 原始特征
    输出: 修正后的概率
    """
```

#### DeepFrontierNN (深度前沿网络)

**文件**: [backend/core/deep_frontier_nn.py](file:///Users/liuxuran/Github/football/backend/core/deep_frontier_nn.py)

**职责**: 深度学习前沿实验模型

### 7.4 子模型

#### 半全场模型

**文件**: [backend/scripts/sub_model_halftime.py](file:///Users/liuxuran/Github/football/backend/scripts/sub_model_halftime.py)

**职责**: 预测上半场+全场组合结果(9种)

#### 比分模型

**文件**: [backend/scripts/sub_model_score.py](file:///Users/liuxuran/Github/football/backend/scripts/sub_model_score.py)

**职责**: 预测具体比分分布

#### 让球模型

**文件**: [backend/scripts/sub_model_handicap.py](file:///Users/liuxuran/Github/football/backend/scripts/sub_model_handicap.py)

**职责**: 让球胜平负概率预测

### 7.5 模型训练与验证

#### 训练脚本

**文件**: [backend/scripts/run_train.py](file:///Users/liuxuran/Github/football/backend/scripts/run_train.py)

```python
def train_lr_model():
    """
    训练逻辑回归融合模型
    
    流程:
        1. 加载历史比赛数据(30K+)
        2. 构建特征矩阵(48维)
        3. 训练LR(L-BFGS-B, L1正则)
        4. 保存权重到 data/weights/lr/
        5. 验证准确率
    """
```

#### 验证框架

**文件**: [backend/monitor/validation_engine.py](file:///Users/liuxuran/Github/football/backend/monitor/validation_engine.py)

```python
class ValidationEngine:
    """
    验证引擎
    
    功能:
        - 计算方向准确率
        - 计算Brier Score
        - 生成校准曲线
        - Walk-forward验证
    """
    
    def validate_predictions(
        self,
        predictions: List[Prediction],
        actual_results: List[str],
    ) -> ValidationReport:
        """
        验证预测准确率
        
        Returns:
            ValidationReport: 包含准确率、Brier Score、校准曲线
        """
```

---

## 8. 数据采集系统

### 8.1 数据源状态

| 数据源 | 状态 | 说明 |
|--------|------|------|
| sporttery.cn | ❌ 已死 | HTTP 567 WAF拦截，永久封锁 |
| zgzcw.com | ✅ 正常 | 百家欧赔(37家)，免费无限制 |
| 500.com | ✅ 正常 | 百家欧赔(20+家)，免费无限制 |
| the-odds-api.com | ✅ 正常 | 国际赔率API，500 credits/月 |
| football-data.org | ✅ 正常 | 赛果/积分榜API |

### 8.2 采集架构

```
Tier 1 (免费层):
  ├── zgzcw.com (30分钟间隔)
  ├── 500.com (30分钟间隔)
  └── football-data.org (2小时缓存)

Tier 2 (额度层):
  └── the-odds-api.com (预算管理，避免超支)

Tier 3 (兜底层):
  └── 合成赔率(基于Elo + 历史对战)
```

### 8.3 采集器详解

#### Zgzcw采集器

**文件**: [backend/ingestion/zgzcw_source.py](file:///Users/liuxuran/Github/football/backend/ingestion/zgzcw_source.py)

**采集流程**:
```
1. 访问 live.zgzcw.com
2. 解析HTML获取比赛列表
3. 提取欧赔/亚盘/竞彩SP
4. 队名标准化(resolve_team_name)
5. 存入数据库(matches + match_bookmaker_odds)
```

**关键函数**:

```python
def collect_zgzcw_odds(db: Session) -> Dict:
    """
    采集zgzcw百家欧赔
    
    Returns:
        {"updated": int, "matches": int, "bookmakers": int}
    """
```

#### 500.com采集器

**文件**: [backend/ingestion/wubaibai_source.py](file:///Users/liuxuran/Github/football/backend/ingestion/wubaibai_source.py)

**采集流程**: 类似zgzcw，解析500.com页面

#### Odds API采集器

**文件**: [backend/ingestion/odds_collector.py](file:///Users/liuxuran/Github/football/backend/ingestion/odds_collector.py)

**预算管理**:
```python
class OddsApiBudget:
    """
    Odds API预算管理
    
    功能:
        - 记录请求次数
        - 计算剩余额度
        - 防止超支(500 credits/月)
    """
```

#### 赛果同步

**文件**: [backend/ingestion/result_sync.py](file:///Users/liuxuran/Github/football/backend/ingestion/result_sync.py)

```python
def sync_match_results(db: Session) -> Dict:
    """
    同步比赛结果
    
    数据源:
        - football-data.org API
        - 手动录入
    
    Returns:
        {"synced": int, "failed": int}
    """
```

#### 伤病数据同步

**文件**: [backend/ingestion/injury_sync.py](file:///Users/liuxuran/Github/football/backend/ingestion/injury_sync.py)

```python
def sync_injury_data(db: Session) -> Dict:
    """
    同步伤病数据
    
    数据源:
        - 手动录入
        - 新闻抓取(未来)
    
    Returns:
        {"updated": int}
    """
```

### 8.4 数据清洗

**文件**: [backend/ingestion/data_cleaner.py](file:///Users/liuxuran/Github/football/backend/ingestion/data_cleaner.py)

```python
class DataCleaner:
    """
    数据清洗器
    
    功能:
        - 队名标准化(resolve_team_name)
        - 赔率异常检测
        - 缺失数据回填
        - 数据质量门检查
    """
    
    def resolve_team_name(raw_name: str) -> str:
        """
        队名标准化
        
        映射规则:
            - 中文别名 → 标准名
            - 英文别名 → 标准名
            - 缩写 → 全名
        
        数据源: data/team_aliases.yaml
        """
```

---

## 9. 定时任务

### 9.1 任务列表

| 任务名 | 间隔 | 功能 | 文件 |
|--------|------|------|------|
| `collect_zgzcw_job` | 30分钟 | 采集zgzcw百家欧赔 | scheduler.py:46 |
| `collect_500_job` | 30分钟 | 采集500.com百家欧赔 | scheduler.py:64 |
| `collect_odds_tier1_job` | 2小时 | Tier1基础赔率更新 | scheduler.py:82 |
| `collect_odds_tier1_secondary_job` | 2小时 | 二级赔率源更新 | scheduler.py:100 |
| `refresh_odds_job` | 1小时 | 综合赔率刷新 | scheduler.py:120 |
| `predict_upcoming_job` | 1小时 | 为即将开赛比赛生成预测 | scheduler.py:140 |
| `self_heal_job` | 2小时 | 健康自检 + 自愈 | scheduler.py:160 |
| `model_audit_job` | 6小时 | 模型审计 | scheduler.py:180 |
| `train_bet_nn_job` | 12小时 | BetNN自动训练 | scheduler.py:200 |
| `train_sub_models_job` | 12小时 | 子模型自动训练 | scheduler.py:220 |
| `drift_check_job` | 6小时 | 策略漂移检测 | scheduler.py:240 |
| `param_optimize_job` | 24小时 | 参数寻优 | scheduler.py:260 |
| `validation_job` | 6小时 | 验证数据更新 | scheduler.py:280 |
| `scrape_jingcai_job` | 2小时 | 竞彩数据采集 | scheduler.py:300 |
| `collect_form_job` | 6小时 | 球队近期状态采集 | scheduler.py:320 |
| `sync_results_job` | 6小时 | 赛果同步 | scheduler.py:340 |
| `auto_close_issues_job` | 1小时 | 自动关闭过期期号 | scheduler.py:360 |
| `sporttery_sync_job` | 6小时 | sporttery同步 | scheduler.py:380 |

### 9.2 任务实现示例

```python
def collect_zgzcw_job():
    """
    从中国足彩网(zgzcw.com)采集百家欧赔。
    一次采集覆盖37家公司，包括竞彩官方/澳门/香港马会。
    免费、无API key、无请求限制。
    """
    from zgzcw_source import collect_zgzcw_odds
    
    with DBSession() as db:
        result = collect_zgzcw_odds(db)
        updated = result.get("updated", 0)
        total = result.get("matches", 0)
        if updated > 0:
            logger.info(f"[zgzcw] Updated {updated}/{total} matches")
```

```python
def self_heal_job():
    """
    健康自检 + 自愈。
    每2小时运行一次HealthDaemon.run_all_checks()。
    """
    from health_daemon import HealthDaemon
    
    daemon = HealthDaemon()
    report = daemon.run_all_checks()
    
    if report.overall != "ok":
        logger.warning(f"[self_heal] Status: {report.overall}")
```

---

## 10. 前端架构

### 10.1 文件结构

```
static/
├── index.html              # SPA入口(266行)
├── app.js                  # 主应用逻辑(1504行)
├── i18n.js                 # 国际化引擎(内置228条中文翻译)
├── api_client.js           # API调用封装
├── api_client.js.map       # Source map
├── input.css               # Tailwind源文件
├── tailwind.css            # 编译后样式
├── legal.html              # 法律页面
├── logo.png                # Logo图片
├── og-image.png            # OG图片
├── openapi.json            # OpenAPI规范
│
├── locales/                # 6语言翻译文件
│   ├── zh.json             # 中文(228条)
│   ├── en.json             # 英文(228条)
│   ├── fr.json             # 法文(228条)
│   ├── es.json             # 西班牙文(228条)
│   ├── de.json             # 德文(228条)
│   └── it.json             # 意大利文(228条)
│
├── src/                    # 源文件
│   ├── api_client.ts       # TypeScript API客户端
│   ├── api/
│   │   └ schema.ts         # OpenAPI生成的类型定义
│   └── components/         # UI组件
│       ├── AdvisorChat.js  # AI分析聊天组件
│       ├── AuthModal.js    # 认证模态框
│       ├── CopilotBubble.js # Copilot气泡
│       ├── FeedCard.js     # Feed卡片
│       ├── FeedbackBoard.js # 留言板
│       ├── MatchCard.js    # 比赛卡片
│       ├── MatchDetailModal.js # 比赛详情模态框
│       ├── MonitorDashboard.js # 监控仪表盘
│       ├── ProMatchCard.js # 专业比赛卡片
│       ├── RedeemModal.js  # 卡密兑换模态框
│       ├── SettingsModal.js # 设置模态框
│       └── ValidationDashboard.js # 验证仪表盘
│
└── vendor/                 # 第三方库
    ├── alpine.min.js       # Alpine.js(轻量MVVM)
    └── persist.min.js      # Alpine持久化插件
```

### 10.2 SPA路由

通过Tab切换，无URL路由:

| Tab | 功能 | 组件 |
|-----|------|------|
| 在售赛事 | 按期号查看比赛列表、模型预测、策略分析 | MatchCard + MatchDetailModal |
| 验证 | 模型预测 vs 赛果的准确率验证看板 | ValidationDashboard |
| 报告 | 每期赛后的复盘报告 | FeedCard |
| AI分析 | 大模型自然语言分析 | AdvisorChat |
| 留言 | 用户留言板 | FeedbackBoard |

### 10.3 国际化机制

**文件**: [static/i18n.js](file:///Users/liuxuran/Github/football/static/i18n.js)

```javascript
/**
 * 国际化引擎
 * 
 * 特性:
 *   - 内嵌完整中文翻译 cache['zh']
 *   - 其他语言XHR异步加载 /static/locales/{lang}.json
 *   - I18n.t(key, ...args) — %d数字占位, %s字符串占位
 *   - I18n.init() — 自动检测浏览器语言
 *   - data-i18n属性 — 静态HTML元素自动翻译
 *   - i18n:change事件 — 切换语言触发重新渲染
 */

const I18n = {
  cache: {
    'zh': {
      'app.title': 'WC Analytics',
      'match.home_win': '主胜',
      'match.draw': '平局',
      'match.away_win': '客胜',
      // ... 228条翻译
    }
  },
  
  t(key, ...args) {
    /**
     * 翻译函数
     * 
     * Args:
     *   key: 翻译键
     *   args: 占位参数
     * 
     * Returns:
     *   翻译文本
     */
  },
  
  init() {
    /**
     * 初始化
     * 
     * 流程:
     *   1. 检测浏览器语言
     *   2. 加载对应语言文件
     *   3. 应用翻译
     */
  },
  
  change(lang) {
    /**
     * 切换语言
     * 
     * 流程:
     *   1. 加载新语言文件
     *   2. 触发i18n:change事件
     *   3. 重新渲染UI
     */
  }
};
```

### 10.4 主应用逻辑

**文件**: [static/app.js](file:///Users/liuxuran/Github/football/static/app.js)

```javascript
/**
 * WC Analytics Frontend App
 * 简洁量化终端: 列表 + 详情双栏布局
 */

Alpine.data('app', () => ({
  // ─── 状态 ───
  matches: [],
  filter: 'upcoming',
  loading: false,
  selectedId: null,
  selectedMatch: null,
  preview: null,
  previewLoading: false,
  selectedStrategy: null,
  strategyLoading: false,
  bankroll: 1000,
  riskTier: 'balanced',
  mobileView: 'list',
  
  // ─── 预测数据 ───
  spfPrediction: null,
  rqPrediction: null,
  scorePrediction: null,
  goalsPrediction: null,
  halfPrediction: null,
  
  // ─── 混合模型信号 ───
  collapseProb: 0,
  bigScoreWarning: false,
  upsetSignal: null,
  portfolios: [],
  
  // ─── 计算属性 ───
  get stakeAmount() {
    if (!this.selectedStrategy || !this.selectedStrategy.is_recommended) return 0;
    return this.bankroll * (this.selectedStrategy.stake_pct || 0);
  },
  
  get predictions() {
    if (!this.selectedMatch) return [];
    return this.selectedMatch.predictions || [];
  },
  
  // ─── 方法 ───
  async init() {
    await I18n.init();
    await this.loadMatches();
  },
  
  async loadMatches() {
    this.loading = true;
    try {
      let resp;
      if (['today', 'tomorrow'].includes(this.filter)) {
        resp = await WCApi.Data.getMatches(undefined, undefined, undefined, this.filter);
      } else if (this.filter === 'upcoming') {
        resp = await WCApi.Data.getMatches('future');
      } else {
        resp = await WCApi.Data.getMatches(this.filter);
      }
      this.matches = resp || [];
    } catch (e) {
      console.error('Load matches failed', e);
    } finally {
      this.loading = false;
    }
  },
  
  async selectMatch(id) {
    if (this.selectedId === id) return;
    this.selectedId = id;
    this.mobileView = 'detail';
    
    // 加载比赛详情、预测、策略
    const m = this.matches.find(x => x.id === id);
    this.selectedMatch = m;
    
    // 加载预测
    await this.loadPredictions(id);
    
    // 加载策略
    await this.loadStrategy(id);
  },
  
  async loadPredictions(matchId) {
    try {
      const predictions = await WCApi.Data.getMatchPredictions(matchId);
      this.spfPrediction = predictions.find(p => p.play_type === 'SPF');
      this.rqPrediction = predictions.find(p => p.play_type === 'RQ');
      this.scorePrediction = predictions.find(p => p.play_type === 'SCORE');
      this.goalsPrediction = predictions.find(p => p.play_type === 'GOALS');
      this.halfPrediction = predictions.find(p => p.play_type === 'HALF');
    } catch (e) {
      console.error('Load predictions failed', e);
    }
  },
  
  async loadStrategy(matchId) {
    this.strategyLoading = true;
    try {
      const strategy = await WCApi.Data.getMatchStrategy(
        matchId, 
        this.riskTier, 
        this.bankroll
      );
      this.selectedStrategy = strategy;
    } catch (e) {
      console.error('Load strategy failed', e);
    } finally {
      this.strategyLoading = false;
    }
  },
}));
```

### 10.5 API客户端

**文件**: [static/src/api_client.ts](file:///Users/liuxuran/Github/football/static/src/api_client.ts)

```typescript
/**
 * API客户端
 * 
 * 封装所有API调用，自动处理认证、错误、重试
 */

export class WCApi {
  static Auth = {
    async login(email: string, password: string): Promise<User> {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, password}),
      });
      if (!resp.ok) throw new Error('Login failed');
      const data = await resp.json();
      localStorage.setItem('token', data.access_token);
      return data.user;
    },
    
    async me(): Promise<User | null> {
      const token = localStorage.getItem('token');
      if (!token) return null;
      const resp = await fetch('/api/auth/me', {
        headers: {'Authorization': `Bearer ${token}`},
      });
      if (!resp.ok) return null;
      return await resp.json();
    },
  };
  
  static Data = {
    async getMatches(status?: string, type?: string, limit?: number, filter?: string): Promise<Match[]> {
      const params = new URLSearchParams();
      if (status) params.append('status', status);
      if (type) params.append('match_type', type);
      if (limit) params.append('limit', limit.toString());
      if (filter) params.append('filter', filter);
      
      const resp = await fetch(`/api/matches?${params}`);
      if (!resp.ok) throw new Error('Failed to fetch matches');
      return await resp.json();
    },
    
    async getMatchPredictions(matchId: number): Promise<Prediction[]> {
      const resp = await fetch(`/api/matches/${matchId}/predictions`);
      if (!resp.ok) throw new Error('Failed to fetch predictions');
      return await resp.json();
    },
    
    async getMatchStrategy(matchId: number, riskTier: string, bankroll: number): Promise<Strategy> {
      const params = new URLSearchParams();
      params.append('risk_tier', riskTier);
      params.append('bankroll', bankroll.toString());
      
      const resp = await fetch(`/api/matches/${matchId}/strategy?${params}`);
      if (!resp.ok) throw new Error('Failed to fetch strategy');
      return await resp.json();
    },
  };
}
```

---

## 11. 部署与运维

### 11.1 服务器信息

| 属性 | 值 |
|------|-----|
| 提供商 | Oracle Cloud (免费VPS) |
| IP | 129.146.124.72 |
| 用户 | ubuntu |
| 域名 | football.nett.to (Let's Encrypt SSL) |
| 时区 | UTC |

### 11.2 服务架构

```
用户 ─── HTTPS:443 ───▶ Nginx ───▶ http://127.0.0.1:8000 ───▶ uvicorn (FastAPI)
                         │
                      football.nett.to SSL (Let's Encrypt)
```

### 11.3 Nginx配置

**路径**: `/etc/nginx/sites-enabled/football.conf`

```nginx
server {
    server_name football.nett.to;
    listen 443 ssl;
    
    ssl_certificate /etc/letsencrypt/live/football.nett.to/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/football.nett.to/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }
}
```

### 11.4 Systemd服务

**路径**: `/etc/systemd/system/football.service`

```ini
[Unit]
Description=WC Analytics Football Prediction API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Github/football/backend
Environment=PYTHONPATH=/home/ubuntu/Github/football/backend
ExecStart=/home/ubuntu/Github/football/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 11.5 环境变量

**路径**: `/home/ubuntu/Github/football/backend/.env`

| 变量 | 说明 | 示例 |
|------|------|------|
| `SECRET_KEY` | JWT签名密钥(>=32字符) | `your-secret-key-here` |
| `ADMIN_API_KEY` | 管理接口API Key | `admin-api-key` |
| `ALLOWED_ORIGINS` | CORS白名单 | `https://football.nett.to` |
| `DEBUG` | 生产环境必须false | `false` |
| `WC_ENV` | 开启生产模式 | `production` |
| `DATABASE_URL` | 数据库URL | `sqlite:///./data/football.db` |

### 11.6 服务命令

```bash
# 查看状态
sudo systemctl status football.service

# 重启服务
sudo systemctl restart football.service

# 查看日志
sudo journalctl -u football.service -n 50 --no-pager
sudo journalctl -u football.service -f  # 实时跟踪

# 启动/停止
sudo systemctl start football.service
sudo systemctl stop football.service

# Nginx命令
sudo nginx -t  # 测试配置
sudo nginx -s reload  # 重载配置
sudo tail -f /var/log/nginx/access.log  # 查看访问日志
```

### 11.7 数据同步流程

**本地→服务器数据同步** (手动):

```bash
# 本地执行(中国IP)
cd /Users/liuxuran/Github/football/backend
python3 sync_jc_to_server.py
```

**流程**:
```
本地(China IP) ─── sync_jc_to_server.py ───▶ 服务器(Oracle US)
    │                                             │
    ├── zgzcw可采集                         存储到 football.db
    ├── 500.com可采集
    └── sporttery.cn不可用
```

**sync_jc_to_server.py工作流**:
1. 本地运行 `collect_zgzcw_odds()` 采集百家欧赔
2. 本地运行 `collect_500_odds()` 补充赔率
3. 本地运行 `predict_upcoming()` 生成预测
4. 通过SCP将 `football.db` 上传到服务器
5. SSH到服务器执行 `systemctl restart football.service`

---

## 12. 依赖关系

### 12.1 Python依赖

**文件**: [backend/requirements.txt](file:///Users/liuxuran/Github/football/backend/requirements.txt)

| 包名 | 版本 | 用途 |
|------|------|------|
| `fastapi` | 0.115.0 | Web框架 |
| `uvicorn[standard]` | 0.32.0 | ASGI服务器 |
| `sqlalchemy` | 2.0.36 | ORM |
| `psycopg2-binary` | 2.9.9 | PostgreSQL驱动 |
| `python-jose[cryptography]` | 3.3.0 | JWT认证 |
| `passlib` | 1.7.4 | 密码哈希 |
| `bcrypt` | 3.2.2 | 密码加密 |
| `python-multipart` | 0.0.17 | 表单处理 |
| `email-validator` | 2.3.0 | 邮箱验证 |
| `pydantic` | 2.9.2 | 数据验证 |
| `pydantic-settings` | 2.6.1 | 配置管理 |
| `slowapi` | 0.1.9 | 速率限制 |
| `stripe` | 11.3.0 | 支付集成(未启用) |
| `python-dotenv` | 1.0.1 | 环境变量 |
| `apscheduler` | 3.10.4 | 定时任务 |
| `certifi` | >=2024.0.0 | SSL证书 |
| `httpx` | 0.28.1 | HTTP客户端 |
| `numpy` | 2.1.3 | 数值计算 |
| `pandas` | 2.2.3 | 数据处理 |
| `scipy` | >=1.14.0,<2.0 | 科学计算 |
| `torch` | >=2.2.0 | 神经网络 |
| `soccerdata` | >=1.9.0 | 足球数据采集 |
| `sse-starlette` | >=1.6.5 | SSE推送 |
| `google-genai` | >=2.8.0 | Gemini API |
| `beautifulsoup4` | (隐式) | HTML解析 |
| `lxml` | (隐式) | XML解析 |

### 12.2 前端依赖

**文件**: [package.json](file:///Users/liuxuran/Github/football/package.json)

| 包名 | 版本 | 用途 |
|------|------|------|
| `openapi-typescript` | ^7.13.0 | OpenAPI类型生成 |
| `tailwindcss` | ^4 | CSS框架 |
| `@tailwindcss/cli` | ^4.3.0 | Tailwind CLI |
| `alpinejs` | (vendor) | MVVM框架 |
| `persist` | (vendor) | Alpine持久化 |

### 12.3 系统依赖

| 软件 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| Node.js | 14+ | 前端构建 |
| Nginx | 1.18+ | 反向代理 |
| SQLite | 3.35+ | 数据库 |
| systemd | 245+ | 服务管理 |

### 12.4 模块依赖图

```
main.py
  ├── database.models (ORM)
  ├── database.config (配置)
  ├── api.routers.* (路由)
  ├── core.prediction_engine (预测)
  ├── strategy.strategy_pipeline (策略)
  ├── monitor.scheduler (调度)
  ├── monitor.health_daemon (健康)
  ├── ingestion.* (采集)
  └── utils.* (工具)

prediction_engine.py
  ├── features.feature_builder (特征)
  ├── features.elo_model (Elo)
  ├── features.poisson_model (Poisson)
  ├── features.market_model (市场)
  ├── features.form_markov_model (状态)
  ├── features.h2h_model (历史)
  ├── fusion.logistic_fusion (LR融合)
  └── core.calibrator (校准)

strategy_pipeline.py
  ├── core.calibrator (校准)
  ├── strategy.edge_calculator (边际)
  ├── strategy.position_sizer (仓位)
  └── strategy.risk_manager (风控)

scheduler.py
  ├── ingestion.zgzcw_source (zgzcw采集)
  ├── ingestion.wubaibai_source (500采集)
  ├── ingestion.odds_collector (赔率采集)
  ├── core.prediction_engine (预测)
  ├── monitor.health_daemon (健康)
  └── monitor.model_audit (审计)
```

---

## 13. 开发指南

### 13.1 本地开发环境

```bash
# 1. 克隆仓库
git clone https://github.com/VariableLab/football.git
cd football/backend

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑.env，填入SECRET_KEY和ADMIN_API_KEY

# 5. 初始化数据库
python3 -c "from database.models import init_db; init_db(); print('OK')"

# 6. 启动开发服务器
uvicorn main:app --reload --port 8000

# 7. 访问 http://127.0.0.1:8000
```

### 13.2 前端构建

```bash
# 构建CSS
npm run build:css

# 构建JS
npm run build:js

# 构建类型定义
npm run build:types

# 全量构建
npm run build

# 监听模式
npm run watch:css
npm run watch:js
```

### 13.3 测试

```bash
# 运行所有测试
cd backend
python3 -m pytest tests/ -v

# 或使用pytest.ini配置
cd ..
pytest

# 运行单个测试
pytest tests/test_prediction_engine.py -v

# 运行带覆盖率
pytest --cov=core --cov=features --cov=fusion tests/
```

### 13.4 代码部署

```bash
# 1. 本地开发和测试
cd /Users/liuxuran/Github/football
git add -A && git commit -m "描述改动"
git push origin master

# 2. SSH到服务器
ssh ubuntu@129.146.124.72

# 3. 拉取和重启
cd /home/ubuntu/Github/football
git pull origin master
sudo systemctl restart football.service

# 4. 验证
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

### 13.5 常见操作

#### 查看健康状态

```bash
# 简单健康检查
curl http://football.nett.to/api/health

# 详细自检报告
curl http://football.nett.to/api/health/detailed

# 赔率覆盖
curl -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/odds/status
```

#### 手动触发操作

```bash
# 刷新赔率
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/odds/refresh

# 训练神经网络
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/bet-nn/train

# 训练子模型
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/sub-models/train-all

# 触发漂移检测
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/strategy/monitor/check

# 参数寻优
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/strategy/optimize

# 关闭过期期号
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/jingcai/issues/auto-close

# 数据质量审计
curl -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/data-quality

# 数据清洗(预览)
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"dry_run": true}' http://football.nett.to/api/admin/data-clean
```

#### 管理竞彩期号

```bash
# 创建期号
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"issue_id":"JC20260520","issue_type":"jingcai","sale_start":"...","sale_end":"...","match_codes":[...]}' \
  http://football.nett.to/api/jingcai/issues

# 生成预测
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" \
  http://football.nett.to/api/jingcai/issues/JC20260520/predict

# 录入开奖结果
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"results": [...], "prizes": {...}, "draw_at": "..."}' \
  http://football.nett.to/api/jingcai/issues/JC20260520/results

# 验证
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" \
  http://football.nett.to/api/jingcai/issues/JC20260520/verify
```

### 13.6 Git分支策略

- `master` — 生产分支，直接部署到服务器
- 功能开发在本地分支，完成后squash merge到master
- 无develop/staging等中间环境

### 13.7 SSH配置

```bash
# 服务器SSH key路径
~/.ssh/server_key

# 连接
ssh -i ~/.ssh/server_key ubuntu@129.146.124.72
```

---

## 附录

### A. 关键文件速查

| 文件 | 行数 | 功能 |
|------|------|------|
| [backend/main.py](file:///Users/liuxuran/Github/football/backend/main.py) | 2077 | FastAPI路由 + 业务逻辑 |
| [backend/database/models.py](file:///Users/liuxuran/Github/football/backend/database/models.py) | 546 | 数据模型(16个ORM类) |
| [backend/monitor/scheduler.py](file:///Users/liuxuran/Github/football/backend/monitor/scheduler.py) | 1423 | 调度器(30+定时任务) |
| [backend/api/schemas.py](file:///Users/liuxuran/Github/football/backend/api/schemas.py) | ~100 | Pydantic响应模型 |
| [backend/monitor/health_daemon.py](file:///Users/liuxuran/Github/football/backend/monitor/health_daemon.py) | ~500 | 自愈守护进程 |
| [backend/core/prediction_engine.py](file:///Users/liuxuran/Github/football/backend/core/prediction_engine.py) | ~400 | 预测引擎 |
| [backend/features/feature_builder.py](file:///Users/liuxuran/Github/football/backend/features/feature_builder.py) | ~200 | 特征拼接器 |
| [backend/fusion/logistic_fusion.py](file:///Users/liuxuran/Github/football/backend/fusion/logistic_fusion.py) | ~300 | LR融合 |
| [backend/strategy/strategy_pipeline.py](file:///Users/liuxuran/Github/football/backend/strategy/strategy_pipeline.py) | ~250 | 策略管线 |
| [static/app.js](file:///Users/liuxuran/Github/football/static/app.js) | 1504 | 前端SPA逻辑 |
| [static/i18n.js](file:///Users/liuxuran/Github/football/static/i18n.js) | ~200 | 国际化引擎 |

### B. 性能指标

| 指标 | 回测最佳值 | 生产实测值 | 目标 |
|------|-----------|-----------|------|
| SPF方向准确率 | 56.6% | ~50% | ≥55% |
| Brier Score | ~0.185 | — | ≤0.190 |
| 淘汰赛准确率 | 49.3% | — | ≥45% |

### C. 项目已完成事项

- ✅ 6语言i18n全站国际化(228条翻译 × 6语言)
- ✅ English README.md + 中文 README_ZH.md
- ✅ 30s宣传视频(TTS语音 + GIF预览)
- ✅ Product Hunt上线准备(4张截图 + OG meta)
- ✅ 模型3层融合(Elo + 特征 + 市场校准)
- ✅ 分层策略(4种风险偏好 + Kelly仓位)
- ✅ 赛前锁定 + 赛后验证
- ✅ 自愈守护进程
- ✅ 百家欧赔采集(zgzcw + 500.com)
- ✅ 竞彩期号全生命周期(创建→预测→开奖→验证→复盘)
- ✅ 智能串关推荐(EV排序)
- ✅ 滚球赔率 + SSE推送
- ✅ AI分析助手(qwen3.5-397b)
- ✅ Let's Encrypt SSL + Nginx反代
- ✅ 卡密付费系统

---

## 结语

WC Analytics是一个完整的足球比赛概率校准研究框架，涵盖了从数据采集、特征工程、模型训练、策略分析到前端展示的完整链路。项目采用3层融合架构(Elo基线 + LR融合 + 校准)，结合自动化运维(自检自愈守护进程)，实现了学术研究级别的概率预测系统。

**核心设计理念**:
- ✅ 所有输出为数学概率，不构成投注建议
- ✅ 赛前锁定可追溯，赛后验证闭环
- ✅ 完全开源，可复现验证

**技术亮点**:
- 48维高精度特征工程
- L1正则化自动特征选择
- 分段线性概率校准
- Kelly仓位 + 4档风险分层
- 自检自愈守护进程

**未来优化方向**:
- 多worker兼容(Redis迁移)
- PostgreSQL迁移
- 自动CI/CD
- 数据源冗余

---

**文档维护**: 请定期更新此Wiki文档，保持与代码同步。

**贡献指南**: 参考 [CONTRIBUTING.md](file:///Users/liuxuran/Github/football/CONTRIBUTING.md)

**许可证**: CC BY-NC-SA 4.0 (学术研究用途)