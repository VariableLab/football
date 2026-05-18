# WC Analytics — 项目功能报告

**生成时间**: 2026-05-09
**版本**: v0.1.0
**状态**: 可测试运行

---

## 一、系统概览

世界杯竞彩足球预测系统，基于 FastAPI + SQLite + Vanilla JS 架构。

- **后端**: Python FastAPI, SQLAlchemy, APScheduler
- **前端**: 原生 JS + Tailwind CSS v4 预编译
- **数据**: SQLite WAL 模式，258 支球队，5464 场历史比赛
- **预测引擎**: Elo + Poisson + 市场赔率 集成融合模型

---

## 二、核心功能清单

### 2.1 数据层

| 功能 | 状态 | 说明 |
|------|------|------|
| 球队数据 | 已完成 | 258 支球队，含 Elo/fifa_rank/flag |
| 历史比赛 | 已完成 | 5464 场 (EPL/LaLiga/SerieA/Bundesliga/Ligue1) |
| xG 估算 | 已完成 | 94队真实xG + 122队Elo回归 + 默认值 |
| 竞彩赔率 | 已完成 | sporttery API 5池(had/hhad/crs/ttg/hafu) |
| 外部赔率 | 已完成 | Tier1-3 分级采集 + 收盘赔率 |

### 2.2 预测引擎

| 功能 | 状态 | 说明 |
|------|------|------|
| SPF(胜平负) | 已完成 | Dixon-Coles 修正 + 平局通胀修正 |
| RQ(让球胜平负) | 已完成 | handicap 参数化 |
| Score(比分) | 已完成 | Poisson 独立概率矩阵 |
| Goals(总进球) | 已完成 | 0-6桶 + 7+尾部 |
| Half(半全场) | 已完成 | 主主/主平/主客/平等9种 |
| 集成融合 | 已完成 | elo:0.15 + poisson:0.35 + players:0.15 + market:0.35 |
| 淘汰赛修正 | 已完成 | R16/QF/SF/F/3P 分阶段参数 |
| 动态市场权重 | 已完成 | 有真实赔率时 market 自动提升至 50% |

### 2.3 付费墙

| 功能 | 状态 | 说明 |
|------|------|------|
| 策略端点付费检查 | 已完成 | `/api/matches/{id}/strategy` 需 is_paid |
| 赛后自动开放 | 已完成 | FINISHED 状态跳过付费检查 |
| 付费过期自动降级 | 已完成 | paid_until < now → is_paid=false |
| 届卡(60天) | 已完成 | WC26-XXXX 格式卡密兑换 |
| 单场卡(7天) | 已完成 | MATCH 类型临时开放 |
| 前端锁定遮罩 | 已完成 | lock-blur + 兑换按钮 |
| 导航栏登录/退出 | 已完成 | 未登录显示"登录"按钮 |

### 2.4 前端

| 功能 | 状态 | 说明 |
|------|------|------|
| 比赛列表 | 已完成 | 卡片式布局，概率条+比分标签 |
| 分页 | 已完成 | 30条/页，分页控件 |
| 筛选Tab | 已完成 | 全部/今日/明日/世界杯/热身赛/竞彩 |
| 比赛详情Modal | 已完成 | 5玩法Tab切换 + 策略区 + 结果横幅 |
| 竞彩视图 | 已完成 | 按期号分组 + 各玩法EV展示 |
| 骨架屏Loading | 已完成 | 6卡片占位动画 |
| Toast通知 | 已完成 | 错误/成功/信息3种类型 |
| 模型验证看板 | 已完成 | 准确率/校准曲线/逐场验证 |
| 系统健康告警 | 已完成 | 赔率过期/调度器异常显示 |

### 2.5 安全

| 功能 | 状态 | 说明 |
|------|------|------|
| JWT认证 | 已完成 | HS256, 7天有效期, iss/aud验证 |
| 全局限流 | 已完成 | 60次/分钟 (slowapi) |
| 精细化限流 | 已完成 | 注册5/h, 登录10/h, 兑换10/h |
| 生产异常处理 | 已完成 | 防止堆栈信息泄露 |
| 安全头中间件 | 已完成 | X-Content-Type-Options等 |
| 可选认证 | 已完成 | get_optional_user 支持付费墙 |

### 2.6 法律合规

| 功能 | 状态 | 说明 |
|------|------|------|
| 免责声明 | 已完成 | /static/legal.html?p=disclaimer |
| 隐私政策 | 已完成 | /static/legal.html?p=privacy |
| 用户协议 | 已完成 | /static/legal.html?p=terms |
| 注册同意 | 已完成 | 勾选框 + 前端校验 |
| 首页底栏链接 | 已完成 | 3个法律页面链接 |

### 2.7 运维监控

| 功能 | 状态 | 说明 |
|------|------|------|
| 健康检查 | 已完成 | 数据库+调度器+赔率新鲜度+告警 |
| 告警系统 | 已完成 | alert_manager 持久化+去重 |
| 赔率采集告警 | 已完成 | 失败/过期触发告警 |
| 预测引擎告警 | 已完成 | 生成失败触发告警 |
| 数据库备份 | 已完成 | 每日3:00, SQLite backup API |
| 结构化日志 | 已完成 | logger.py 统一格式 |

### 2.8 调度器任务

| 任务 | 频率 | 说明 |
|------|------|------|
| 赔率 Tier1 | 每2小时 | 基础数据检查 |
| 赔率 Tier2 | 08:00/20:00 | Odds API全量 |
| 赔率 Tier3 | 12:00 + 赛前4h | 焦点战加采 |
| 收盘赔率 | 每15分钟 | 赛前90min内 |
| 预测锁定 | 每小时 | 赛前48h内比赛 |
| 比赛监控 | 每分钟 | 状态变更检测 |
| 数据库备份 | 每日3:00 | SQLite backup |
| xG估算 | 每日5:00 | Elo回归填充 |
| 竞彩同步 | 09:00/15:00 | sporttery API |
| 球队状态 | 每日6:00 | football-data.org |
| FBref同步 | 每周日4:00 | 高级统计 |
| Elo同步 | 每周日4:30 | Club Elo等级分 |
| 准确率计算 | 每小时 | SPF方向准确率 |

---

## 三、E2E 测试结果

| 测试项 | 结果 | 说明 |
|--------|------|------|
| /api/health | PASS 200 | status=degraded (调度器未启动) |
| /api/teams | PASS 200 | 258 teams |
| 用户注册 | PASS 200 | 返回 JWT |
| 用户登录 | PASS 200 | 返回 JWT |
| /api/auth/me (未付费) | PASS 200 | is_paid=False |
| Strategy 无认证 | PASS 403 | 付费墙生效 |
| Strategy 未付费 | PASS 403 | 付费墙生效 |
| 卡密兑换(Tournament) | PASS 200 | 60天届卡 |
| /api/auth/me (已付费) | PASS 200 | is_paid=True |
| Strategy 已付费 | PASS 200 | 4策略+5预测 |
| 已结束比赛自动开放 | PASS 200 | 无需付费 |
| 卡密兑换(Match) | PASS 200 | 7天单场卡 |
| Match用户is_paid | PASS 200 | is_paid=True |
| 首页 | PASS 200 | 7799 bytes |
| 法律页面 | PASS 200 | 5568 bytes |
| Tailwind CSS | PASS 200 | 31701 bytes |

---

## 四、已知限制与待完成项

### 待完成

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P1 | 赛后结果自动同步 | 调度器 sync_results_job 为 TODO |
| P2 | 赔率更新后自动重算预测 | 当前赔率更新不触发预测刷新 |
| P3 | 赛前72h 赔率频率提升 | 当前仅在赛前90min内每15分钟 |
| P3 | JWT refresh token | 当前7天过期后需重新登录 |
| P4 | Admin 赔率校验 | odds > 1.0 服务端校验 |
| P4 | HTTPS 强制 | 部署层配置 |
| P4 | GCS 数据库备份 | 需 GCS 凭证 |
| P5 | Walk-forward 自动权重学习 | 模型迭代框架已就绪 |

### 技术限制

- **预测准确率**: ~61% (SPF方向), 不可用于投注决策
- **球员数据**: 602条记录但 key_injuries/squad_fatigue 多为默认值
- **SQLite**: 单写者限制，不适合高并发
- **调度器**: 需在 lifespan 中启动，当前健康检查显示 stopped

---

## 五、快速启动

```bash
cd /Users/liuxuran/Github/football/backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

浏览器打开: http://127.0.0.1:8000

### 测试流程

1. 点击右上角「登录」按钮 → 注册新账户
2. 浏览比赛列表（分页），切换 Tab 筛选
3. 点击比赛卡片 → 查看概率预测（免费）
4. 策略区域显示「需要付费解锁」
5. 生成测试卡密: `python3 -c "import sys; sys.path.insert(0,'.'); from models import SessionLocal; from license_manager import create_license_keys; from models import LicenseType; db=SessionLocal(); k=create_license_keys(db,LicenseType.TOURNAMENT); print(k[0].key); db.close()"`
6. 点击「兑换」→ 输入卡密 → 策略解锁
7. 查看模型验证看板（底部）
