"""
数据源采集相关定时任务
- 百家欧赔采集 (zgzcw, 500.com)
- 赔率分级采集 (tier1/2/3, 收盘赔率)
- 开盘赔率更新, 滚球赔率采集
"""
from datetime import datetime, timedelta, timezone

from database.models import Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("scheduler.data_collection")


# ────────────────────────────
# Task 0: Zgzcw — 中国足彩网百家欧赔采集（每30分钟）
# ────────────────────────────
def collect_zgzcw_job():
    """从中国足彩网（zgzcw.com）采集百家欧赔。"""
    from zgzcw_source import collect_zgzcw_odds
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_zgzcw_odds(db)
        updated = result.get("updated", 0)
        total = result.get("matches", 0)
        if updated > 0:
            logger.info(f"[zgzcw] Updated {updated}/{total} matches with real odds from 37 companies")
        else:
            logger.debug(f"[zgzcw] No new odds (scanned {total} matches)")


def collect_500_job():
    """从 500.com 采集百家欧赔。"""
    from wubaibai_source import collect_500_odds
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_500_odds(db)
        updated = result.get("updated", 0)
        total = result.get("matches", 0)
        if updated > 0:
            logger.info(f"[500] Updated {updated}/{total} matches with real odds from 20+ bookmakers")
        else:
            logger.debug(f"[500] No new odds (scanned {total} matches)")


# ────────────────────────────
# Task 1a-d: 赔率分级采集
# ────────────────────────────
def collect_odds_tier1_job():
    """Tier 1: 免费/基础层 — 每2小时"""
    from odds_collector import collect_odds_tier1_primary
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_odds_tier1_primary(db)
        stale = result.get("stale_matches", 0)
        if stale > 0:
            logger.warning(f"[odds-tier1] {stale} matches have stale odds")
        logger.info(f"[odds-tier1] OK | budget: {result.get('budget_remaining', 'N/A')} credits remaining")


def collect_odds_tier2_job():
    """Tier 2: 付费全量层 — 每天08:00和20:00"""
    from odds_collector import collect_odds_tier2_premium
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_odds_tier2_premium(db)
        if result.get("skipped"):
            logger.info(f"[odds-tier2] Skipped: {result.get('reason')}")
            return
        anomalies = result.get("anomalies", [])
        for a in anomalies:
            logger.warning(
                f"[odds-tier2] {a.match_id} | {a.source} | "
                f"{a.direction}: {a.old_odds:.2f} -> {a.new_odds:.2f} "
                f"({a.change_pct:+.1%}) [{a.severity}]"
            )
        logger.info(
            f"[odds-tier2] Fetched {result.get('matches_count', 0)} matches, "
            f"used {result.get('credits_used', 0)} credit, "
            f"remaining {result.get('budget_remaining', 0)}"
        )


def collect_odds_tier3_job():
    """Tier 3: 焦点加采层 — 每天12:00 + 赛前4h自动"""
    from odds_collector import collect_odds_tier3_focus
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_odds_tier3_focus(db)
        if result.get("skipped"):
            logger.info(f"[odds-tier3] Skipped: {result.get('reason')}")
            return
        anomalies = result.get("anomalies", [])
        for a in anomalies:
            logger.warning(
                f"[odds-tier3] {a.match_id} | {a.source} | "
                f"{a.direction}: {a.old_odds:.2f} -> {a.new_odds:.2f} "
                f"({a.change_pct:+.1%}) [{a.severity}]"
            )
        logger.info(
            f"[odds-tier3] Focus fetch: {result.get('matches_count', 0)} matches, "
            f"used {result.get('credits_used', 0)} credit, "
            f"remaining {result.get('budget_remaining', 0)}"
        )


def collect_closing_odds_job():
    """收盘赔率采集（赛前90分钟内，每15分钟）"""
    from odds_collector import collect_closing_odds_for_upcoming
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        result = collect_closing_odds_for_upcoming(db, hours=4)
        if result.get("skipped"):
            logger.debug(f"[closing-odds] Skipped: {result.get('reason')}")
        else:
            logger.info(
                f"[closing-odds] Updated {result.get('matches_updated', 0)}/"
                f"{result.get('matches_processed', 0)} matches"
            )


def update_opening_odds_job():
    """批量更新开盘赔率"""
    from odds_tracker import OddsTracker
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        tracker = OddsTracker(db)
        updated = tracker.batch_update_opening_odds()
        if updated > 0:
            logger.info(f"[opening-odds] Updated {updated} matches")


def live_odds_poll_job():
    """滚球赔率采集"""
    from live_odds_feed import LiveOddsFeed, get_odds_bus
    from database.config import get_settings

    settings = get_settings()
    feed = LiveOddsFeed(
        bus=get_odds_bus(),
        poll_interval=settings.LIVE_ODDS_POLL_INTERVAL,
        use_simulated=True,
    )
    n = feed.poll_once()
    if n > 0:
        logger.info(f"[live-odds] {n} updates collected")


def auto_focus_trigger():
    """每小时检查是否有比赛在4h内开始，自动触发 tier3 加采"""
    now = datetime.now(timezone.utc)
    from monitor.scheduler.jobs import DBSession
    from database.models import Match, MatchStatus

    with DBSession() as db:
        focus_matches = db.query(Match).filter(
            Match.kickoff_at.between(now, now + timedelta(hours=4)),
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING])
        ).all()
        if focus_matches:
            logger.info(f"[auto-focus] {len(focus_matches)} matches within 4h, triggering tier3")
            collect_odds_tier3_job()
