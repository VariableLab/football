"""
健康检查、监控、数据质量相关任务
"""
from utils.logger import get_logger

logger = get_logger("scheduler.health")


def health_check_wrapper():
    """自检+自修引擎：每10分钟"""
    from monitor.health_daemon import health_check_job
    health_check_job()


def strategy_monitor_wrapper():
    """策略漂移监控：每天 22:00"""
    from monitor.strategy_monitor import strategy_monitor_job
    strategy_monitor_job()


def injury_sync_wrapper():
    """伤停数据同步：每天 08:00"""
    from ingestion.injury_sync import InjurySync
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        sync = InjurySync(db)
        updated = sync.sync_upcoming(days=7)
        logger.info(f"[injury-sync] Updated {updated} teams")
        cleared = sync.clear_stale_injuries(days=3)
        logger.info(f"[injury-sync] Cleared {cleared} stale entries")


def zgzcw_sync_wrapper():
    """Zgzcw 竞彩数据同步：每30分钟（合并原3个冗余任务）"""
    from ingestion.zgzcw_jc_sync import sync_jc_matches
    result = sync_jc_matches()
    if result.get("created") or result.get("updated"):
        logger.info(
            f"[zgzcw-sync] Synced: {result.get('created', 0)} created, "
            f"{result.get('updated', 0)} updated"
        )
    else:
        logger.debug("[zgzcw-sync] No new data")
