# WC Analytics — 架构与运维手册

> 最后更新: 2026-05-20

## 目录

1. [项目概览](#1-项目概览)
2. [技术栈](#2-技术栈)
3. [目录结构](#3-目录结构)
4. [架构设计](#4-架构设计)
5. [数据模型](#5-数据模型)
6. [前端架构](#6-前端架构)
7. [后端 API > 模块详解](#7-后端-api--模块详解)
8. [定时任务 (Scheduler)](#8-定时任务-scheduler)
9. [数据采集](#9-数据采集)
10. [机器学习模型管线](#10-机器学习模型管线)
11. [服务器部署与运维](#11-服务器部署与运维)
12. [日常操作流程](#12-日常操作流程)
13. [开发工作流](#13-开发工作流)
14. [后续优化方向](#14-后续优化方向)

---

## 1. 项目概览

**仓库地址**: [https://github.com/VariableLab/football](https://github.com/VariableLab/football)
**线上地址**: [https://football.nett.to](https://football.nett.to)
**许可**: CC BY-NC-SA 4.0 (学术研究用途)

### 一句话描述

WC Analytics 是一个开源的足球比赛概率校准研究框架，对 31K+ 历史比赛、462 支球队构建 3 层融合概率建模系统。所有输出为数学概率值，赛前锁定可追溯，不构成投注建议。

### 核心功能

- **竞彩在售赛事展示** — 按期号显示所有在售赛事及其 SPF/让球/比分/总进球/半全场赔率
- **模型预测** — 主力模型 + 子模型（半全场/比分/让球）的融合概率预测
- **分层策略** — Kelly 仓位 + 风险分层（保守/均衡/积极/投机）
- **智能串关推荐** — 基于 EV 值排序的最优串关组合
- **滚球赔率** — SSE 实时推送 + 滚球对冲警报
- **模型验证看板** — 与赛果的命中率对比、校准曲线
- **AI 分析助手** — 通过大模型接口进行自然语言赛事分析
- **预测报告** — 每期模型预测 vs 开奖结果的复盘
- **用户系统** — 注册/登录/卡密兑换付费
- **i18n** — 6 语言支持（中/英/法/西/德/意）

---

## 2. 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 后端框架 | **FastAPI** (Python 3.10+) | REST API + SSE + 静态文件服务 |
| 数据库 | **SQLite** (WAL 模式) | 单文件数据库，所有数据统配 |
| ORM | **SQLAlchemy 2.0** | 数据模型与查询 |
| 任务调度 | **APScheduler** | 定时采集/刷新/自愈 |
| 前端 | 原生 **HTML/CSS/JS** | SPA 风格，Tailwind CSS + 自定义主题 |
| 代理 | **Nginx** | HTTPS 终止 + API 反向代理 |
| 系统服务 | **systemd** | 进程管理 + 自重启 |
| 模型 | **PyTorch** (神经网络) + **scipy** (校准) | BetNN + 子模型 + 概率校准 |

---

## 3. 目录结构

```
football/
├── backend/                          # Python 后端
│   ├── main.py                       # FastAPI 入口/路由
│   ├── models.py                     # SQLAlchemy ORM 模型
│   ├── schemas.py                    # Pydantic 请求/响应模型
│   ├── config.py                     # 配置管理（环境变量）
│   ├── auth.py                       # JWT 认证
│   ├── scheduler.py                  # APScheduler 定时任务中心
│   ├── deploy.sh                     # 初始部署脚本
│   ├── sync_jc_to_server.py          # 本地→服务器数据同步
│   ├── requirements.txt              # Python 依赖
│   ├── .env.example                  # 环境变量模板
│   │
│   ├── prediction_engine.py          # 核心预测引擎
│   ├── calibrator.py                 # 概率校准（Platt/Isotonic）
│   ├── fusion_strategy.py            # 3 层融合策略
│   ├── strategy_pipeline.py          # Kelly 仓位管线
│   ├── tiered_strategy.py            # 分层策略分析
│   ├── optimal_combo.py              # 串关推荐
│   ├── position_sizer.py             # 仓位计算器
│   ├── edge_calculator.py            # EV/Edge 计算
│   ├── risk_manager.py               # 风险控制
│   │
│   ├── health_daemon.py              # 自愈守护进程
│   ├── alert_manager.py              # 告警管理
│   ├── model_audit.py                # 模型审计
│   ├── validation_engine.py          # 验证引擎
│   ├── data_cleaner.py               # 数据清洗
│   │
│   ├── odds_collector.py             # 赔率采集器
│   ├── odds_tracker.py               # 赔率追踪
│   ├── live_odds_feed.py             # 滚球赔率推送
│   ├── live_hedge_engine.py          # 滚球对冲
│   ├── hedge_engine.py               # 套利扫描
│   ├── zgzcw_source.py               # 中国足彩网采集
│   ├── wubaibai_source.py            # 500.com 采集
│   │
│   ├── bet_nn.py                     # 预测神经网络
│   ├── sub_model_halftime.py         # 半全场子模型
│   ├── sub_model_score.py            # 比分预测子模型
│   ├── sub_model_handicap.py         # 让球子模型
│   ├── residual_nn.py                # 残差网络
│   ├── xg_estimator.py               # xG 估算
│   │
│   ├── scheduler.py                  # 调度中心
│   ├── strategy_monitor.py           # 策略漂移监控
│   ├── param_optimizer.py            # 参数寻优
│   ├── weight_learner.py             # 权重学习
│   │
│   ├── jingcai_predictor.py          # 竞彩期号预测
│   ├── form_collector.py             # 近期状态采集
│   ├── injury_sync.py                # 伤病数据
│   ├── result_sync.py                # 赛果同步
│   ├── license_manager.py            # 卡密管理
│   ├── sse.py                        # SSE 事件推送
│   └── admin.py                      # 管理路由
│
├── static/                           # 前端静态文件
│   ├── index.html                    # SPA 入口
│   ├── app.js                        # 主应用逻辑（1500+ 行）
│   ├── i18n.js                       # 国际化引擎（内置中文翻译）
│   ├── api_client.js                 # API 客户端
│   ├── input.css / tailwind.css      # Tailwind 样式
│   ├── legal.html                    # 法律页面
│   ├── locales/                      # 6 语言翻译文件
│   │   ├── zh.json / en.json / fr.json / es.json / de.json / it.json
│   └── src/                          # Tailwind 源文件
│
├── docs/                             # 文档
├── screenshots/                      # PH 截图
├── tests/                            # 测试
│
├── demo.mp4                          # 30s 宣传视频（带语音）
├── demo_preview.gif                  # 5s GIF 预览
└── README.md / README_ZH.md         # 中英文 README
```

---

## 4. 架构设计

### 架构总览

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│    Nginx     │────▶│   FastAPI    │
│  (SPA SPA)   │◀────│  (HTTPS +    │◀────│  Uvicorn ①   │
│              │     │   反代)      │     │   :8000      │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                    ┌─────────────────────────────┤
                    │              │              │
               ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
               │ SQLite  │   │ APSched │   │PyTorch  │
               │ WAL 模式 │   │ 定时器  │   │ 模型    │
               └─────────┘   └─────────┘   └─────────┘
                    │
                    │  外部数据源
                    ├── zgzcw.com (中国足彩网百家欧赔, 免费)
                    ├── 500.com (百家欧赔, 免费)
                    ├── football-data.org (赛果/积分, API)
                    ├── the-odds-api.com (国际赔率, 付费额度)
                    └── deepstock.zone.id (AI 分析, API)
```

> ① 单 worker 部署。多 worker 需迁移 live-odds 全局状态至 Redis。

### 设计决策

| 决策 | 理由 |
|------|------|
| SQLite (非 PostgreSQL) | 单服务器部署，无高并发写入需求，运维零成本 |
| 原生 JS (非 React/Vue) | 页面功能聚焦、无复杂状态管理，减少构建步骤 |
| i18n 内嵌中文翻译 | 避免 XHR 异步加载导致首屏翻译缺失 |
| 单 worker | live-odds SSE 用模块级全局变量，多 worker 需 Redis |
| Nginx 反代 + Let's Encrypt | 轻量级 HTTPS 终止，管理简便 |

---

## 5. 数据模型

完整定义在 `backend/models.py` (546 行)，核心表：

### 核心表

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `matches` | 比赛主表 | `home_team_id, away_team_id, kickoff_at, odds_home/draw/away, status, match_type, actual_outcome, actual_goals` |
| `teams` | 球队 | `name, flag, fifa_rank, elo, form_factor` |
| `predictions` | 预测记录 | `match_id, play_type, probabilities(JSON), model_version, locked_at` |
| `users` | 用户 | `email, password_hash, is_paid, paid_until` |
| `jingcai_issues` | 竞彩期号 | `issue_id, issue_type, status(on_sale/drawn/verified), sale_start/end` |
| `jingcai_issue_matches` | 期号↔比赛关联 | `issue_id, match_id, sequence, handicap, rq_odds/score_odds/goals_odds/half_odds(JSON)` |
| `odds_history` | 赔率历史快照 | `match_id, odds_home/draw/away, source, recorded_at` |
| `match_bookmaker_odds` | 多博彩公司赔率 | `match_id, bookmaker, odds_home/draw/away, updated_at` |
| `feedback` | 用户留言 | `user_id, category, content, likes` |
| `license_keys` | 卡密 | `key, license_type, is_used, used_by` |
| `user_settings` | 用户偏好设置 | `risk_tier, default_play_type, show_ev` |

### 数据流

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
```

---

## 6. 前端架构

### 文件结构

```
static/
├── index.html        # 266 行, SPA 入口
├── app.js            # 1504 行, 主应用逻辑
├── i18n.js           # 国际化引擎 (内置 228 条中文翻译)
├── api_client.js     # API 调用封装
├── locales/          # 6 个语言 JSON (各 228 条)
└── tailwind.css      # 编译后样式
```

### SPA 路由 (通过 Tab 切换, 无 URL 路由)

| Tab | 功能 |
|-----|------|
| 在售赛事 | 按期号查看比赛列表、模型预测、策略分析 |
| 验证 | 模型预测 vs 赛果的准确率验证看板 |
| 报告 | 每期赛后的复盘报告 |
| AI 分析 | 大模型自然语言分析 |
| 留言 | 用户留言板 |

### 国际化机制

- `i18n.js` 内嵌完整中文翻译 `cache['zh']` — 脚本加载即可用
- 其他语言通过 XHR 异步加载 `/static/locales/{lang}.json`
- `I18n.t(key, ...args)` — `%d` 数字占位, `%s` 字符串占位
- `I18n.init()` — 自动执行, 检测浏览器语言 / localStorage
- `data-i18n` 属性 — 静态 HTML 元素自动翻译
- `i18n:change` 事件 — 切换语言时触发, 前端重新渲染

### 主题

温暖优雅风格，暗色模式为主，使用 Tailwind CSS 的 `charcoal` / `beige` / `cream` / `warm-gray` 色板。

---

## 7. 后端 API > 模块详解

### 路由模块

| 前缀 | 文件 | 功能 |
|------|------|------|
| `/` | `main.py` | 首页文件服务 |
| `/api/auth/*` | `main.py:178-218` | 用户注册/登录/获取信息 |
| `/api/matches/*` | `main.py:263-545` | 比赛列表/详情/策略/赔率变动 |
| `/api/jingcai/*` | `main.py:1037-1262` | 竞彩期号 CRUD/预测/开奖/验证/报告/串关 |
| `/api/validation/*` | `main.py:816-838` | 模型验证/校准曲线/玩法准确率 |
| `/api/feedback/*` | `main.py:1460-1561` | 留言 CRUD/点赞 |
| `/api/live-odds/*` | `main.py:597-709` | 滚球赔率 SSE/轮询/启动/停止 |
| `/api/live-hedge/*` | `main.py:715-810` | 滚球对冲/仓位/计算 |
| `/api/arbitrage` | `main.py:551-583` | 跨博彩公司套利扫描 |
| `/api/health` | `main.py:1389-1448` | 健康检查 + 详细报告 |
| `/api/settings/*` | `main.py:1567-1628` | 用户偏好设置 |
| `/api/bet-nn/*` | `main.py:1634-1664` | 预测神经网络状态/推理/训练 |
| `/api/sub-models/*` | `main.py:1670-1743` | 子模型(半全场/比分/让球)状态/训练 |
| `/api/predictions/*` | `main.py:1715-1722` | 综合预测报告 |
| `/api/strategy/*` | `main.py:1816-1920` | 策略参数/分层分析/寻优/漂移监控 |
| `/api/sporttery/*` | `main.py:1749-1810` | sporttery.cn 数据同步 |
| `/api/chat` | `main.py:2003-2063` | AI 分析（调用 openai 兼容 API） |
| `/api/admin/*` | `main.py:1304-1383` | 赔率刷新/数据质量审计/清洗 |
| `/api/license/*` | `main.py:223-239` | 卡密兑换 |

### 核心模块详解

#### 7.1 预测引擎 (`prediction_engine.py`)

3 层融合策略：
1. **Elo 基线** — 基于 Elo 评级系统的基础概率
2. **特征模型** — 使用球队历史统计数据训练的梯度提升模型
3. **市场校准** — 使用市场赔率进行 Platt/Isotonic 校准

#### 7.2 策略管线 (`strategy_pipeline.py`)

```
predictions → Kelly 仓位计算 → EV 排序 → 风险分层 → 输出最优策略
```

- Kelly 分数根据风险偏好调整（保守 0.25 → 投机 1.0）
- 检出 `edge > 0` 的正期望投注
- 计算 VaR (95%) / CVaR (95%)

#### 7.3 健康守护 (`health_daemon.py`)

自检项目（每 15 分钟）：
- 数据库完整性（SQLite 自检）
- 赔率新鲜度（距上次采集时间）
- 调度器任务状态（46 个任务）
- 数据完整性（比赛数量 / 缺赔率 / 缺球队数据）
- 模型漂移（方向准确率 vs 阈值 48%）
- 滚动自愈（当准确率下降时启动重新训练）

#### 7.4 赔率采集 (`odds_collector.py`)

三级采集架构：
- **Tier 1 (免费)** — zgzcw.com + 500.com 百家欧赔，30 分钟间隔
- **Tier 2 (额度)** — the-odds-api.com，预算管理，避免超支
- **Tier 3 (兜底)** — 合成赔率（基于 Elo + 历史对战）

#### 7.5 竞彩期号 (`jingcai_predictor.py`)

- `create_issue` — 创建期号并关联比赛
- `predict_issue` — 为整期比赛生成预测
- `record_draw_result` — 录入开奖结果
- `verify_issue` — 验证模型 vs 开奖

#### 7.6 AI 聊天 (`main.py:2003-2063`)

调用 `deepstock.zone.id` 的 OpenAI 兼容 API（qwen3.5-397b-a17b 模型），传入比赛上下文和预测数据，返回自然语言分析。

---

## 8. 定时任务 (Scheduler)

所有任务注册于 `backend/scheduler.py` (1423 行)。

### 任务列表

| 任务 | 间隔 | 功能 |
|------|------|------|
| `collect_zgzcw_job` | 30 分钟 | 采集中国足彩网百家欧赔 |
| `collect_500_job` | 30 分钟 | 采集 500.com 百家欧赔 |
| `collect_odds_tier1_job` | 2 小时 | Tier 1 基础赔率更新 |
| `collect_odds_tier1_secondary_job` | 2 小时 | 二级赔率源更新 |
| `refresh_odds_job` | 1 小时 | 综合赔率刷新 |
| `predict_upcoming_job` | 1 小时 | 为即将开赛比赛生成预测 |
| `self_heal_job` | 2 小时 | 健康自检 + 自愈 |
| `model_audit_job` | 6 小时 | 模型审计 |
| `train_bet_nn_job` | 12 小时 | BetNN 自动训练 |
| `train_sub_models_job` | 12 小时 | 子模型自动训练 |
| `drift_check_job` | 6 小时 | 策略漂移检测 |
| `param_optimize_job` | 24 小时 | 参数寻优 |
| `validation_job` | 6 小时 | 验证数据更新 |
| `scrape_jingcai_job` | 2 小时 | 竞彩数据采集 |
| `collect_form_job` | 6 小时 | 球队近期状态采集 |
| `sync_results_job` | 6 小时 | 赛果同步 |
| `auto_close_issues_job` | 1 小时 | 自动关闭过期期号 |
| `sporttery_sync_job` | 6 小时 | sporttery 同步 |

> 注：大部分采集任务依赖中国源（zgzcw / 500.com / sporttery.cn），海外服务器无法直连。线上服务器通过这些 IP 黑名单/ WAF 拦截，数据依赖本地 `sync_jc_to_server.py` 同步。

---

## 9. 数据采集

### 数据源状态

| 数据源 | 状态 | 说明 |
|--------|------|------|
| sporttery.cn | ❌ 已死 | 返回 HTTP 567 WAF 拦截，被永久封锁 |
| zgzcw.com | ✅ 正常 | 百家欧赔（含竞彩官方/澳门/威廉希尔/bet365等 37 家） |
| 500.com | ✅ 正常 | 百家欧赔补充 |
| the-odds-api.com | ✅ 正常 | 国际赔率 API，免费额度 500 credits/月 |
| football-data.org | ✅ 正常 | 赛果/积分榜 API |

### 同步流程

**本地→服务器数据同步** (手动)：
```
本地 (China IP) ─── sync_jc_to_server.py ───▶ 服务器 (Oracle US)
    │                                                 │
    ├── zgzcw 可采集                           存储到 football.db
    ├── 500.com 可采集
    └── sporttery.cn 不可用
```

`sync_jc_to_server.py` 工作流：
1. 本地运行 `collect_zgzcw_odds()` 采集百家欧赔
2. 本地运行 `collect_500_odds()` 补充赔率
3. 本地运行 `predict_upcoming()` 生成预测
4. 通过 SCP 将 `football.db` 上传到服务器
5. SSH 到服务器执行 `systemctl restart football.service`

---

## 10. 机器学习模型管线

### 主力模型架构

```
输入特征
  ├── Elo 评分 (主队/客队/差值)
  ├── 历史对战记录
  ├── 近期状态因子 (近 10 场胜负/winstreak/losestreak)
  ├── FIFA 排名
  ├── 主客场因素
  └── 伤病影响 (injury_sync)
      │
      ▼
┌─────────────────────────────────────┐
│  Layer 1: Elo 基线模型               │
│  P(胜|平|负) = f(Elo_diff)           │
├─────────────────────────────────────┤
│  Layer 2: 特征模型 (PyTorch)         │
│  GBM + 神经网络混合                  │
├─────────────────────────────────────┤
│  Layer 3: 市场校准 (Platt/Isotonic)  │
│  使用开盘赔率作为先验校准             │
└─────────────────────────────────────┘
      │
      ▼
校准后概率 (SPF/RQ/Score/Goals/Half)
```

### 子模型

- **半全场模型** — 预测上半场 + 全场组合结果（9 种）
- **比分模型** — 预测具体比分分布
- **让球模型** — 让球胜平负概率

### BetNN (预测神经网络)

PyTorch 残差网络，用于对整期比赛的预测评分和推荐排序。输入为主模型概率 + 赔率 + 球队特征。

### 策略漂移监控

通过对比滚动窗口（最近比赛）与基线快照的准确率差异，检测模型是否发生漂移。漂移检测到后触发自动重训。

### 参数寻优

对 Kelly 系数/安全阈值/VaR 等策略参数进行网格搜索，以历史回测 ROI 为优化目标。

---

## 11. 服务器部署与运维

### 服务器信息

| 属性 | 值 |
|------|-----|
| 提供商 | Oracle Cloud (免费 VPS) |
| IP | `129.146.124.72` |
| 用户 | `ubuntu` |
| 域名 | `football.nett.to` (Let's Encrypt SSL) |
| 时区 | UTC |

### 服务架构

```
用戶 ─── HTTPS :443 ───▶ Nginx ───▶ http://127.0.0.1:8000 ───▶ uvicorn (FastAPI)
                         │
                      football.nett.to SSL (Let's Encrypt)
```

### Nginx 配置

路径: `/etc/nginx/sites-enabled/football.conf`

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

### Systemd 服务

路径: `/etc/systemd/system/football.service`

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

> ⚠️ 注意：deploy.sh 中写的 service 名是 `wc-analytics`，但实际系统上是 `football.service`。

### 环境变量（服务器）

通过 SSH 编辑 `/home/ubuntu/Github/football/backend/.env`：

| 变量 | 说明 |
|------|------|
| `SECRET_KEY` | JWT 签名密钥（>=32 字符） |
| `ADMIN_API_KEY` | 管理接口的 API Key |
| `ALLOWED_ORIGINS` | CORS 白名单 |
| `DEBUG=false` | 生产环境必须为 false |
| `WC_ENV=production` | 开启生产模式 |

### 服务命令

```bash
# 状态
sudo systemctl status football.service

# 重启
sudo systemctl restart football.service

# 日志
sudo journalctl -u football.service -n 50 --no-pager
sudo journalctl -u football.service -f                        # 实时跟踪

# 启动/停止
sudo systemctl start football.service
sudo systemctl stop football.service
```

### Nginx 命令

```bash
# 测试配置
sudo nginx -t

# 重载
sudo nginx -s reload

# 查看访问日志
sudo tail -f /var/log/nginx/access.log
```

---

## 12. 日常操作流程

### 12.1 数据同步（每天）

从本地（中国 IP）手动向服务器同步数据：

```bash
# 本地执行
cd /Users/liuxuran/Github/football/backend
python3 sync_jc_to_server.py
```

**注意**: 服务器在海外，无法直连中国数据源（zgzcw.com, 500.com 等需要中国 IP）。`sync_jc_to_server.py` 在本地运行采集和预测，然后通过 SCP 将数据库推送到服务器。

### 12.2 代码部署

```bash
# 1. 本地开发和测试
cd /Users/liuxuran/Github/football
git add -A && git commit -m "描述改动"
git push origin master

# 2. SSH 到服务器
ssh ubuntu@129.146.124.72

# 3. 拉取和重启
cd /home/ubuntu/Github/football
git pull origin master
sudo systemctl restart football.service

# 4. 验证
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

### 12.3 查看健康状态

```bash
# 简单健康检查
curl http://football.nett.to/api/health

# 详细自检报告
curl http://football.nett.to/api/health/detailed

# 赔率覆盖
curl -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/odds/status
```

### 12.4 手动触发操作

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

# 数据清洗（预览）
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"dry_run": true}' http://football.nett.to/api/admin/data-clean
```

### 12.5 管理竞彩期号

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

---

## 13. 开发工作流

### 本地开发环境

```bash
# 1. 克隆
git clone https://github.com/VariableLab/football.git
cd football/backend

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 SECRET_KEY 和 ADMIN_API_KEY

# 4. 初始化数据库
python3 -c "from models import init_db; init_db(); print('OK')"

# 5. 启动开发服务器
uvicorn main:app --reload --port 8000

# 6. 访问 http://127.0.0.1:8000
```

### 迭代流程

```
1. 发现问题 / 需求 ──▶ 2. 本地分支开发 ──▶ 3. 测试 ──▶ 4. commit + push
                                                          │
                                                          ▼
5. SSH 到服务器 ──▶ 6. git pull ──▶ 7. systemctl restart
```

### 测试

```bash
cd backend
python3 -m pytest tests/ -v

# 或使用 pytest.ini 配置
cd ..
pytest
```

---

## 14. 后续优化方向

### 高优先级

| 项目 | 说明 |
|------|------|
| **多 worker 兼容** | 将 live-odds 全局变量迁移至 Redis，支持水平扩展 |
| **PostgreSQL 迁移** | SQLite 在写入频繁时性能受限，迁移到 PG 提升并发 |
| **自动 CI/CD** | GitHub Actions 自动化测试 + 服务器部署 |
| **数据源冗余** | sporttery.cn 已死，寻找替代的中文竞彩数据源 |

### 中优先级

| 项目 | 说明 |
|------|------|
| **前端 SSR** | 迁移到 React/Vue 以获得更好的开发体验和 SSR |
| **Swagger 文档** | 生产环境开放 API 文档 (/docs) |
| **WebSocket** | SSE 升级为 WebSocket 获得双向通信 |
| **模型版本管理** | 模型参数和权重的版本化存储 |
| **Docker 化** | 容器化部署，消除环境差异 |

### 长期方向

| 项目 | 说明 |
|------|------|
| **自动化数据同步** | 通过 cron job 替代手动 `sync_jc_to_server.py` |
| **用户付费集成** | Stripe 支付链路（已配置但未启用） |
| **移动端 App** | 基于目前 API 构建 iOS/Android 客户端 |
| **多联赛覆盖** | 扩展至英超/西甲/意甲等主流联赛 |
| **API 市场** | 开放 API 供第三方调用 |

---

## 附录

### A. 关键文件速查

| 文件 | 行数 | 功能 |
|------|------|------|
| `backend/main.py` | 2077 | FastAPI 路由 + 业务逻辑 |
| `backend/models.py` | 546 | 数据模型 (16 个 ORM 类) |
| `backend/scheduler.py` | 1423 | 调度器 (30+ 定时任务) |
| `backend/schemas.py` | ~100 | Pydantic 响应模型 |
| `backend/health_daemon.py` | ~500 | 自愈守护进程 |
| `static/app.js` | 1504 | 前端 SPA 逻辑 |
| `static/i18n.js` | ~200 | 国际化引擎 |

### B. SSH 配置

```bash
# 服务器 SSH key 路径
~/.ssh/server_key

# 连接
ssh -i ~/.ssh/server_key ubuntu@129.146.124.72
```

### C. Git 分支策略

- `master` — 生产分支，直接部署到服务器
- 功能开发在本地分支，完成后 squash merge 到 master
- 无 develop/staging 等中间环境

### D. 项目已做事项总结

- ✅ 6 语言 i18n 全站国际化（228 条翻译 × 6 语言）
- ✅ English README.md + 中文 README_ZH.md
- ✅ 30s 宣传视频（TTS 语音 + GIF 预览）
- ✅ Product Hunt 上线准备（4 张截图 + OG meta）
- ✅ 模型 3 层融合（Elo + 特征 + 市场校准）
- ✅ 分层策略（4 种风险偏好 + Kelly 仓位）
- ✅ 赛前锁定 + 赛后验证
- ✅ 自愈守护进程
- ✅ 百家欧赔采集（zgzcw + 500.com）
- ✅ 竞彩期号全生命周期（创建→预测→开奖→验证→复盘）
- ✅ 智能串关推荐（EV 排序）
- ✅ 滚球赔率 + SSE 推送
- ✅ AI 分析助手（qwen3.5-397b）
- ✅ Let's Encrypt SSL + Nginx 反代
- ✅ 卡密付费系统
