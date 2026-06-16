# Odds API 调度 + 手动补齐 SOP

> 用户: 免费版 Odds API key (`e6d7fb4cf3a602c1cd151c7f678a00b1`)
> 限额: 500 credits/month
> 部署时间: 2026-06-17

## 成本结构

| 来源 | 频率 | Credits/次 | 月消耗 |
|------|------|:---------:|:------:|
| Cron 自动 (Tier 2) | 每天 2 次 (08:00 / 20:00) | 1 | ~60 |
| Cron 自动 (Tier 3 焦点) | 每天 1 次 + 赛前 4h | 1 | ~30-60 |
| **手动 CLI 补齐** | 关键时 | 1 | **10-30** |
| 缓冲 | — | — | ~350 |

> 默认每月只用 ~120 credits,留 380 给突发。

## 自动调度(已就绪)

不需要额外配置,scheduler 已设:
- **Tier 2** (08:00, 20:00) — `monitor/scheduler.py:908` 全量 upcoming
- **Tier 3** (12:00 + 赛前 4h) — `monitor/scheduler.py` 焦点战加采

检查是否在跑:
```bash
# 服务器上
ps aux | grep -E "scheduler|main:app" | grep -v grep
# 应该有 scheduler 进程
```

## 手动补齐(新)

```bash
# 1. 进去 backend
cd /path/to/football/backend

# 2. 激活 venv (如果用了)
source venv/bin/activate

# 3. 默认 (未来 24h, 最多 5 场)
python -m scripts.odds_api_fetch

# 4. 关键战备 (未来 6h, 最多 10 场)
python -m scripts.odds_api_fetch --hours 6 --max 10

# 5. 指定联赛
python -m scripts.odds_api_fetch --league "EPL" --max 8

# 6. 先看会调什么
python -m scripts.odds_api_fetch --dry-run

# 7. 看预算
cat backend/ingestion/.odds_api_budget.json
# 输出: {"year_month": "2026-06", "used": 3}
```

## 触发条件(经验法则)

| 场景 | 是否调 | 命令 |
|------|:-----:|------|
| 重大比赛前 1h | ✅ | `--hours 2 --max 3` |
| 主流联赛周末 | ✅ | `--hours 24 --max 10` |
| 平时 (有 cron 兜底) | ❌ | — |
| 月底剩余 < 50 credits | ❌ | (等下个月) |
| 数据异常 (合成赔率 >50%) | ✅ | `--hours 48 --max 15` |

## Telegram 集成(可选)

可以把手动命令接进 Telegram bot:

```python
# backend/utils/telegram_notifier.py 或 PRO_OPERATOR_MANUAL 加指令
"补齐赔率" -> run "python -m scripts.odds_api_fetch --hours 6 --max 10"
```

## 监控

每周看一次:
```bash
# 1. 剩余 credits
cat backend/ingestion/.odds_api_budget.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(f'本月已用 {d[\"used\"]}/500, 剩余 {500-d[\"used\"]}')"

# 2. oddsapi 真实赔率覆盖率 (跑一次)
python -c "
from database.config import get_settings
from database.models import SessionLocal, Match, OddsHistory
db = SessionLocal()
total = db.query(Match).filter(Match.status == 'scheduled').count()
with_oddsapi = db.query(OddsHistory).filter(OddsHistory.source.like('oddsapi%')).distinct(OddsHistory.match_id).count()
print(f'Scheduled: {total}, oddsapi: {with_oddsapi}, 覆盖率: {with_oddsapi/max(total,1):.1%}')
"
```

## 应急: 月底用完

- 等待下月 1 号 0 点 (UTC),budget 自动 reset 到 0
- 期间只能依赖 cron 抓的 zgzcw / 500.com 等免费源
- 真急用可临时升级到 $29/月 (10K credits),但默认不推荐

## 历史修复

- **2026-06-17**: 注入免费版 key + 新增 `scripts/odds_api_fetch.py` 手动 CLI
- 之前 `.env` 留空 → OddsApiSource 一直 `[oddsapi] No API key configured`
