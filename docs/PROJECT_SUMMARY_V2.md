# WC Analytics 项目总结 v2

> 生成日期: 2026-05-11 | 版本: 0.1.0 | 作者: 项目自动审计

---

## 一、项目概述

WC Analytics 是一个足球竞彩智能分析平台，核心功能是从多数据源采集赔率、运行4子模型融合预测、生成投注策略推荐。目标赛事为2026世界杯，同时覆盖五大联赛历史数据。

**技术栈**: Python 3.10 / FastAPI / SQLAlchemy / SQLite(WAL) / PyTorch / APScheduler / Tailwind CSS v4 / SSE

---

## 二、已实现功能清单

### 2.1 核心预测引擎 (P0)

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| Elo模型 | prediction_engine.py | Elo差→胜率转换 | ✅ 正常 |
| 泊松模型 | prediction_engine.py | Dixon-Coles λ参数→比分矩阵 | ✅ 已修复比分膨胀一致性问题 |
| 球员调整 | prediction_engine.py | 关键球员影响因子 | ⚠️ 无实际球员数据，默认1.0 |
| 市场模型 | prediction_engine.py | 赔率隐含概率提取 | ⚠️ 循环依赖风险(见§四) |
| 融合引擎 | prediction_engine.py | 4子模型线性加权 | ✅ 权重可学习 |
| 概率校准 | calibrator.py | 分段线性校准曲线 | ✅ 默认曲线可用 |
| 边缘计算 | edge_calculator.py | 概率 vs 赔率隐含概率差值 | ✅ |
| 仓位计算 | position_sizer.py | 分数Kelly公式 | ✅ 4档风险 |
| 风险管理 | risk_manager.py | 最大亏损/连败控制 | ✅ |
| 策略管线 | strategy_pipeline.py | 预测→校准→边缘→仓位→风控 | ✅ |

### 2.2 赔率追踪 (P1)

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 赔率采集器 | odds_collector.py | 3层采集(Tier1免费/Tier2付费/Tier3焦点) | ✅ Tier1/2可用 |
| 赔率追踪 | odds_tracker.py | 开盘/收盘赔率快照 | ✅ |
| 套利检测 | odds_tracker.py | 跨博彩公司套利扫描 | ✅ 但数据源少 |

### 2.3 滚球系统 (P2)

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 滚球赔率 | live_odds_feed.py | 实时赔率采集+SSE推送 | ✅ 模拟模式可用 |
| 滚球对冲 | live_hedge_engine.py | 实时对冲方案计算 | ✅ |
| OddsBus | live_odds_feed.py | 零延迟赔率分发 | ✅ |

### 2.4 竞彩集成

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 期号同步 | jingcai_predictor.py | sporttery.cn API同步期号 | ✅ 每日2次 |
| 赔率解析 | jingcai_predictor.py | SPF/RQ/比分/进球/半全场赔率 | ✅ |
| 自动关闭 | main.py | 过售期号自动标记closed | ✅ |

### 2.5 自动化基础设施 (本次新增)

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 自检+自修 | health_daemon.py | 7项自动检查+自动修复 | ✅ 每10分钟 |
| 模型复盘 | model_audit.py | 每日复盘+权重自适应 | ✅ 每日05:30 |
| 投注神经网络 | bet_nn.py | 3层MLP学习投注价值 | ✅ 每日06:30训练 |
| 用户留言板 | models.py + main.py | 4类留言+点赞 | ✅ API可用 |
| 用户设置 | models.py + main.py | 风险偏好+通知配置 | ✅ |
| 冒烟测试 | tests/test_smoke.py | 19个API自动化测试 | ✅ 全部通过 |
| 告警系统 | alert_manager.py | 连续失败检测+赔率新鲜度 | ⚠️ 已有函数，现已被health_daemon激活 |

### 2.6 前端

| 模块 | 功能 | 状态 |
|------|------|------|
| 竞彩首页 | 期号Tab+赔率卡片+概率柱+EV计算 | ✅ |
| 筛选器 | 竞彩在售/全部/今日/明日/世界杯/热身赛 | ✅ |
| 比赛详情Modal | SPF/RQ/比分/进球/半全场Tab | ✅ |
| 策略推荐 | Kelly仓位+概率校准推荐 | ✅ |
| NN投注价值 | 神经网络推荐柱状图 | ✅ |
| 验证看板 | 方向准确率+Brier Score+逐场明细 | ✅ |
| 留言板 | 发帖+分类筛选+点赞 | ⚠️ 前端加载可能因服务器未重启而404 |
| 设置面板 | 风险等级+显示开关 | ✅ |
| SSE实时赔率 | EventSource连接 | ✅ |

---

## 三、调度器任务一览

| ID | 任务 | 频率 | 状态 |
|----|------|------|------|
| collect_odds_tier1 | 基础赔率检查 | 每2小时 | ✅ |
| collect_odds_tier2 | Odds API全量采集 | 08:00/20:00 | ✅ |
| collect_odds_tier3 | 焦点战加采 | 12:00 | ✅ |
| auto_focus_trigger | 赛前4h自动加采 | 每小时 | ✅ |
| collect_closing_odds | 收盘赔率采集 | 每15分钟 | ✅ |
| opening_odds_backfill | 开盘赔率回填 | 03:30 | ✅ |
| live_odds_poll | 滚球赔率采集 | 每30秒 | ✅ 模拟模式 |
| lock_predictions | 预测锁定 | 每小时 | ✅ |
| match_monitor | 比赛状态监控 | 每分钟 | ✅ |
| sync_results | 结果同步 | 每5分钟 | ✅ |
| backup_db | 数据库备份 | 03:00 | ✅ |
| fbref_sync | FBref统计同步 | 周日04:00 | ✅ |
| elo_sync | Club Elo同步 | 周日04:30 | ✅ |
| collect_form | 近期状态采集 | 06:00 | ✅ |
| fill_xg | xG估算填充 | 05:00 | ✅ |
| calc_accuracy | 准确率计算 | 每小时 | ✅ |
| jingcai_sync | 竞彩期号同步 | 09:00/15:00 | ✅ |
| health_check | 自检+自修 | 每10分钟 | ✅ 新增 |
| daily_audit | 模型每日复盘 | 05:30 | ✅ 新增 |
| weekly_audit | 模型每周深度复盘 | 周一06:00 | ✅ 新增 |
| bet_nn_train | 投注NN训练 | 06:30 | ✅ 新增 |
| heartbeat | 心跳 | 每5分钟 | ⚠️ 只日志"OK"不验证 |

---

## 四、已知问题与风险

### 4.1 严重问题 (CRITICAL)

| # | 问题 | 影响 | 详情 |
|---|------|------|------|
| C1 | **82.4%比赛无真实赔率** | 市场模型和Edge计算对25,577场使用Elo合成赔率，形成循环引用 | 仅5,440/31,038场有真实收盘赔率。MarketModel fallback到odds_*时，实际上就是Elo自己的输出再输入回来 |
| C2 | **方向准确率仅46.7%** | 赔率建议的参考价值低 | 全库30,644场: 主胜59.1%/平0%/客胜66.9%。模型从不预测"平"，直接损失25%场次 |
| C3 | **13个修正层中8个始终为默认值** | 泊松模型的精细化调整形同虚设 | 仅Layer1(xG基线)、Layer3(FIFA排名)、Layer7(疲劳)有效。天气/战术/教练/阵容/赛制等全部默认1.0 |
| C4 | **60场WC2026赔率负利润率** | 合成赔率数学上不可能 | Pinnacle标签的60场世界杯赔率overround=-3%到-8%，明显是从Elo生成的假赔率 |

### 4.2 中等问题 (HIGH)

| # | 问题 | 影响 | 详情 |
|---|------|------|------|
| H1 | **JINGCAI_OVERROUND=10% vs 实际12.9%** | 竞彩Edge计算偏乐观3个百分点 | 真实竞彩赔率平均overround 12.9%，代码用10%会高估价值投注 |
| H2 | **historical_importer不填closing_odds** | 3万场历史比赛赔率存在odds_*但closing_odds_*为空 | MarketModel优先用closing_odds，为空则降级到odds_*（可能是开盘价而非收盘价） |
| H3 | **校准曲线是硬编码常量** | 无法随数据增长自动更新 | DEFAULT_CALIBRATION_FACTORS声称来自5330场，但fit_from_db()未在默认流程调用 |
| H4 | **前端留言板加载失败** | 用户看到"加载留言失败" | 原因: 服务器运行旧代码(未重启)，代码本身已验证正确 |
| H5 | **两个api_client.js重复** | 维护混乱 | backend/api_client.js是旧版4.7KB，static/api_client.js是新版6.5KB。前端只加载static版 |

### 4.3 低等问题 (MEDIUM)

| # | 问题 | 影响 |
|---|------|------|
| M1 | prediction_engine.py 1829行 | 超过800行标准，应拆分 |
| M2 | heartbeat只记日志不验证 | 无法检测其他job是否真正运行 |
| M3 | 无Alembic迁移框架 | DB schema变更靠手动 |
| M4 | 无Docker/CI/CD | 部署全靠手动 |
| M5 | requirements.txt缺seleniumbase | odds_collector的Macau/HK爬虫TODO依赖 |

---

## 五、数据真实性评估

### 5.1 数据库统计

| 指标 | 数值 | 真实性 |
|------|------|--------|
| 总比赛数 | 31,038 | ✅ 来自football-data.co.uk历史CSV |
| 已结束有结果 | 30,644 (98.7%) | ✅ 实际比分 |
| 有真实收盘赔率 | 5,440 (17.5%) | ✅ 欧洲博彩公司赔率 |
| 有竞彩赔率 | 55 | ✅ sporttery.cn实时API |
| 有Elo评分 | 374/375 (99.7%) | ✅ Club Elo同步 |
| 有xG数据 | 375/375 (100%) | ⚠️ 大部分是Elo估算，非真实FBref xG |
| 有近期战绩 | 32/375 (8.5%) | ❌ 覆盖率极低 |
| 有非默认战术风格 | 39/375 (10.4%) | ❌ 覆盖率极低 |

### 5.2 预测准确率

| 数据集 | 场次 | 方向准确率 | 备注 |
|--------|------|-----------|------|
| 全库 | 30,644 | **46.7%** | 受"从不预测平"拖累 |
| 有真实赔率的场次 | 5,330 | **54.1%** | 略高于随机(33%)，但离实用(>60%)差距大 |
| 无赔率场次 | 25,314 | 45.1% | Elo+Poisson只能做到这个水平 |
| 去除平局后 | 22,972 | **62.3%** | 主/客胜方向还行 |
| 高概率预测(≥50%) | — | 待统计 | 需model_audit积累数据 |

### 5.3 校准曲线

校准因子来源标注为"5330场walk-forward"，数值与数据库匹配，但**无法独立验证拟合过程**。低概率区(10-15%)的factor=0.42意味着模型说12%概率时实际只有5%——这是合理的Poisson过度自信修正。

### 5.4 竞彩赔率真实性

| 来源 | 场次 | 平均overround | 真实性 |
|------|------|--------------|--------|
| sporttery.cn | 55 | 12.9% | ✅ 真实竞彩赔率 |
| 代码硬编码 | - | 10.0% | ⚠️ 偏低 |
| 欧洲联赛 | 5,385 | 5.4% | ✅ 非竞彩 |

---

## 六、未实现功能

| 优先级 | 功能 | 说明 |
|--------|------|------|
| P0 | **历史赔率回填** | historical_importer已有B365/PS/IW赔率列，需写入closing_odds_*字段，可瞬间让30K场有真实赔率 |
| P0 | **平局预测能力** | 模型从不预测平，直接损失25%场次。需要专门的draw-detection子模型 |
| P1 | **世界杯专项数据** | FIFA排名/国家队大名单/热身赛成绩/小组赛轮换模式 |
| P1 | **图表化展示** | 赔率走势图、概率分布图、回测收益曲线 |
| P1 | **赔率变动通知** | 后端设置字段已有，前端通知UI未实现 |
| P2 | **Macau/HK博彩赔率** | odds_collector有TODO，未实现 |
| P2 | **Alembic迁移** | DB schema变更无版本管理 |
| P2 | **Docker部署** | 无容器化 |
| P2 | **自动化CI/CD** | 无持续集成 |
| P3 | **用户付费系统** | License模型已有，Stripe集成未完成 |
| P3 | **多语言支持** | 仅中文 |

---

## 七、架构评估

### 7.1 优点

- 模块化设计：子模型、校准器、策略管线各自独立
- 自动化覆盖广：21个定时任务覆盖数据采集→预测→验证→修复全链路
- 自检+自修：health_daemon实现了从检测到修复的闭环
- 神经网络学习：bet_nn独立于主引擎，可增量训练
- 前端交互流畅：竞彩卡片、概率柱、SSE实时更新

### 7.2 需要优化

| 方向 | 当前 | 目标 |
|------|------|------|
| 赔率覆盖率 | 17.5%真实 | → 90%+（修复importer） |
| 方向准确率 | 46.7%全库 | → 55%+（加赔率+加平局预测） |
| 修正层活跃度 | 3/13层有效 | → 8/13+（填充球队数据） |
| 代码文件大小 | 最大1829行 | → <800行（拆分） |
| 测试覆盖率 | 19个冒烟测试 | → 80%+单元测试 |

---

## 八、最高ROI改进清单（按投入产出比排序）

| 排名 | 改进 | 预期效果 | 工作量 |
|------|------|----------|--------|
| 1 | 修复historical_importer写入closing_odds | 30K场从17.5%→90%+真实赔率覆盖 | 1天 |
| 2 | 添加draw-detection子模型 | 全库准确率从46.7%→55%+ | 2-3天 |
| 3 | JINGCAI_OVERROUND改为12.9% | 竞彩Edge计算不再偏乐观 | 10分钟 |
| 4 | 删除backend/api_client.js | 消除维护混乱 | 1分钟 |
| 5 | prediction_engine.py拆分 | 可维护性大幅提升 | 1天 |

---

## 九、文件清单

```
backend/
├── main.py              (1422行) FastAPI入口+所有API端点
├── prediction_engine.py (1829行) 4子模型+融合+预测
├── odds_collector.py    (1523行) 3层赔率采集
├── jingcai_predictor.py (1326行) 竞彩期号+赔率
├── models.py            (~530行) SQLAlchemy ORM
├── scheduler.py         (~770行) 21个定时任务
├── config.py            (110行)  Pydantic配置
├── auth.py              密码+JWT
├── calibrator.py        (283行)  概率校准
├── edge_calculator.py   边缘计算
├── position_sizer.py    Kelly仓位
├── risk_manager.py      风险控制
├── strategy_pipeline.py 策略管线
├── odds_tracker.py      赔率追踪
├── hedge_engine.py      对冲引擎
├── live_odds_feed.py    滚球赔率+SSE
├── live_hedge_engine.py 滚球对冲
├── form_collector.py    近期战绩
├── xg_estimator.py      xG估算
├── validation_engine.py 验证引擎
├── health_daemon.py     自检+自修 (NEW)
├── model_audit.py       模型复盘+权重自适应 (NEW)
├── bet_nn.py            投注神经网络 (NEW)
├── alert_manager.py     告警系统
├── license_manager.py   卡密系统
├── admin.py             管理API
├── openfootball_importer.py  数据导入
├── historical_importer.py    历史CSV导入
├── tests/                  (NEW)
│   ├── conftest.py
│   └── test_smoke.py    (19 tests)
static/
├── index.html           前端主页
├── app.js               前端逻辑(~1200行)
├── api_client.js        API封装
├── tailwind.css         编译后样式
└── input.css            Tailwind源
```

---

*文档结束 — 项目 v0.1.0 状态快照，2026-05-11*
