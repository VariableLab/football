"""
Odds API 手动补齐 CLI 工具
============================

用户: 免费版 Odds API (500 credits/month),每天 2 次 cron 自动采集 (08:00 / 20:00)。
     关键时候可手动调本 CLI 补齐数据,严格控制 credits 消耗。

设计原则:
  1. 默认"保守"模式: 1 次 API 调用, 1 credit, 默认 5 场关键比赛
  2. 严格预算: 剩余 credits < 阈值时拒绝执行
  3. 关键比赛定义: 未来 24h 内的 upcoming 比赛, 且当前无真实赔率 (None/0/null)
  4. 可指定: 联赛 (--league) / 时间窗 (--hours) / 最多比赛数 (--max)
  5. 复用 odds_collector 的 OddsApiSource + OddsApiBudget, 不重写逻辑

用法:
    # 默认: 未来 24h, 缺赔率的前 5 场
    python -m scripts.odds_api_fetch

    # 关键战备: 未来 6h, 最多 10 场
    python -m scripts.odds_api_fetch --hours 6 --max 10

    # 指定联赛
    python -m scripts.odds_api_fetch --league "EPL" --max 8

    # dry-run: 只看会调什么, 不真发请求
    python -m scripts.odds_api_fetch --dry-run

    # 强制忽略预算 (不推荐, 除非月末)
    python -m scripts.odds_api_fetch --force

    # JSON 输出 (CI / Telegram 集成)
    python -m scripts.odds_api_fetch --json

成本控制:
    - 默认每次调 1 credit (Odds API /sports/soccer/odds 是 1 credit/market)
    - 500 credits/月 / 2 次 cron/天 = 平均 8 credits/天自动消耗
    - 手动 CLI 默认 5 场 = 1 credit/次, 留 490 credits 给自动任务

exit code:
    0  = ok (成功补齐 ≥ 1 场)
    1  = failed (API 错误 / 网络问题)
    2  = skipped (预算耗尽 / 无关键比赛 / --force 拒绝)
    3  = dry_run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

# ── 路径注入 ──
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CURRENT_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WC Analytics Odds API 手动补齐工具 (免费版友好)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                              # 默认: 未来 24h, 前 5 场
  %(prog)s --hours 6 --max 10           # 未来 6h, 最多 10 场
  %(prog)s --league "EPL" --max 8       # 只补 EPL
  %(prog)s --dry-run                    # 只看不跑
  %(prog)s --min-budget 50 --max 3      # 预算 ≥ 50 才跑, 最多 3 场

注意:
  免费版 Odds API 限额 500 credits/月
  本工具默认 1 credit/次, 严格控制
  详细预算: cat backend/ingestion/.odds_api_budget.json
        """,
    )
    p.add_argument(
        "--hours",
        type=int,
        default=24,
        help="时间窗(小时), 默认 24",
    )
    p.add_argument(
        "--max",
        type=int,
        default=5,
        dest="max_matches",
        help="最多比赛数(默认 5, 防止一次刷爆)",
    )
    p.add_argument(
        "--league",
        type=str,
        default=None,
        help="限定联赛(可选, 如 'EPL' / 'LaLiga')",
    )
    p.add_argument(
        "--min-budget",
        type=int,
        default=20,
        help="最低剩余 credits 阈值, 低于此值拒绝执行(默认 20)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示计划, 不真发请求",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="强制忽略预算检查(不推荐, 月末才用)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="JSON 输出(便于 CI / 监控集成)",
    )
    return p.parse_args()


def _resolve_key_matches(db, hours: int, league: Optional[str], max_matches: int) -> List:
    """
    选出"关键比赛":
      - 未来 `hours` 小时内开赛
      - 状态 = SCHEDULED
      - 当前赔率为空 (无 zgzcw / oddsapi / market 真实来源)
      - 可选: 限定联赛
    按开赛时间升序, 取前 max_matches 场。
    """
    from database.models import Match, MatchStatus, OddsHistory

    now = datetime.utcnow()
    deadline = now + timedelta(hours=hours)

    q = (
        db.query(Match)
        .filter(Match.kickoff_at >= now)
        .filter(Match.kickoff_at <= deadline)
        .filter(Match.status == MatchStatus.SCHEDULED)
    )

    if league:
        q = q.filter(Match.competition == league)

    candidates = q.order_by(Match.kickoff_at.asc()).limit(max_matches * 3).all()

    # 过滤: 没有真实赔率的
    real_sources = ("zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc")
    key_matches = []
    for m in candidates:
        has_real = (
            db.query(OddsHistory)
            .filter(OddsHistory.match_id == m.id)
            .filter(OddsHistory.source.in_(real_sources))
            .first()
        )
        if has_real:
            continue
        if m.odds_home and m.odds_draw and m.odds_away:
            continue
        key_matches.append(m)
        if len(key_matches) >= max_matches:
            break

    return key_matches


def main() -> int:
    args = parse_args()
    started = datetime.utcnow().isoformat()

    # ── 校验 key ──
    try:
        from database.config import get_settings
        settings = get_settings()
    except Exception as e:
        print(f"FATAL: 加载 settings 失败: {e}", file=sys.stderr)
        return 1

    api_key = settings.ODDS_API_KEY
    if not api_key:
        msg = "ODDS_API_KEY 未配置 (检查 backend/.env)"
        if args.json:
            print(json.dumps({"status": "skipped", "reason": msg, "started_at": started},
                             ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {msg}")
        return 2

    # ── 预算检查 ──
    try:
        from ingestion.odds_collector import OddsApiBudget
        budget = OddsApiBudget()
    except Exception as e:
        print(f"FATAL: 加载 OddsApiBudget 失败: {e}", file=sys.stderr)
        return 1

    remaining = 500 - budget._data.get("used", 0)
    if not args.force and remaining < args.min_budget:
        msg = f"剩余 credits {remaining} < 阈值 {args.min_budget}, 拒绝执行 (用 --force 强制)"
        if args.json:
            print(json.dumps({
                "status": "skipped", "reason": "budget_low",
                "remaining_credits": remaining,
                "min_budget": args.min_budget,
                "started_at": started,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"SKIP: {msg}")
        return 2

    # ── 选比赛 ──
    try:
        from database.models import SessionLocal
        db = SessionLocal()
    except Exception as e:
        msg = f"打开 DB 失败: {e}"
        if args.dry_run:
            # dry-run 模式 DB 不可用就当"无关键比赛",不报错
            if args.json:
                print(json.dumps({
                    "status": "dry_run", "matches_planned": 0,
                    "reason": msg, "started_at": started,
                }, ensure_ascii=False, indent=2))
            else:
                print(f"[DRY-RUN] DB 不可用,模拟 0 场: {msg}")
            return 3
        print(f"FATAL: {msg}", file=sys.stderr)
        return 1

    try:
        matches = _resolve_key_matches(db, args.hours, args.league, args.max_matches)
    finally:
        db.close()

    if not matches:
        msg = f"未来 {args.hours}h 内无关键比赛需要补齐"
        if args.json:
            print(json.dumps({
                "status": "skipped", "reason": "no_key_matches",
                "hours_window": args.hours, "league": args.league,
                "started_at": started,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"SKIP: {msg}")
        return 2

    # ── dry-run ──
    if args.dry_run:
        result = {
            "status": "dry_run",
            "matches_planned": len(matches),
            "credits_planned": 1,
            "league": args.league,
            "hours_window": args.hours,
            "remaining_credits": remaining,
            "started_at": started,
            "matches": [
                {
                    "id": m.id,
                    "home": m.home_team.name if m.home_team else "?",
                    "away": m.away_team.name if m.away_team else "?",
                    "kickoff_at": m.kickoff_at.isoformat() if m.kickoff_at else None,
                    "competition": m.competition,
                }
                for m in matches
            ],
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[DRY-RUN] 将调 Odds API 1 次, 补齐 {len(matches)} 场:")
            for m in matches:
                home = m.home_team.name if m.home_team else "?"
                away = m.away_team.name if m.away_team else "?"
                ko = m.kickoff_at.strftime("%m-%d %H:%M") if m.kickoff_at else "?"
                print(f"  - [{m.id}] {home} vs {away} @ {ko} ({m.competition})")
        return 3

    # ── 实际调 ──
    try:
        from ingestion.odds_collector import OddsApiSource
        odds = OddsApiSource(api_key=api_key)
    except Exception as e:
        print(f"FATAL: 加载 OddsApiSource 失败: {e}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始调 Odds API, "
              f"目标 {len(matches)} 场, 当前剩余 {remaining} credits")

    snapshots = odds.fetch_batch(matches)
    if not budget.spend(1):
        print("WARN: budget.spend 失败, 但 API 已调, 记录到日志", file=sys.stderr)

    # ── 写回 DB ──
    updated = 0
    try:
        from database.models import SessionLocal
        db = SessionLocal()
        try:
            for snap in snapshots:
                snap.source = "oddsapi-manual"
                db.add(snap)
            db.commit()
            updated = len(snapshots)
        except Exception as e:
            print(f"ERROR: 写回 DB 失败: {e}", file=sys.stderr)
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        print(f"ERROR: 打开 DB 失败: {e}", file=sys.stderr)

    result = {
        "status": "ok" if updated > 0 else "no_data",
        "requested": len(matches),
        "updated": updated,
        "credits_used": 1,
        "credits_remaining": 500 - budget._data.get("used", 0),
        "started_at": started,
        "finished_at": datetime.utcnow().isoformat(),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成: "
              f"请求 {len(matches)} 场, 补齐 {updated} 场, "
              f"credits {remaining} -> {result['credits_remaining']}")

    return 0 if updated > 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(rc)
