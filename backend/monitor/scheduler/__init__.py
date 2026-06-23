"""
monitor/scheduler package — 向后兼容的调度器入口

所有原有的 scheduler 任务函数和工具都保留在子模块中，
本包负责重新导出它们以保持 `from scheduler import start_scheduler` 等导入不变。
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone

from utils.logger import get_logger

logger = get_logger("scheduler")

# 全局调度器实例（保持向后兼容）
scheduler = BackgroundScheduler()

# 从子模块导入所有任务函数
from monitor.scheduler.jobs import DBSession, cleanup_old_backups, _BACKEND_ROOT

from monitor.scheduler.data_collection import (
    collect_zgzcw_job,
    collect_500_job,
    collect_odds_tier1_job,
    collect_odds_tier2_job,
    collect_odds_tier3_job,
    collect_closing_odds_job,
    update_opening_odds_job,
    live_odds_poll_job,
    auto_focus_trigger,
)

from monitor.scheduler.results import (
    sync_results_job,
    zgzcw_draw_sync_job,
    fill_drawn_issues_job,
    jingcai_auto_verify_wrapper,
    jingcai_realtime_results_wrapper,
)

from monitor.scheduler.external_sync import (
    collect_fbref_stats_job,
    collect_elo_ratings_job,
    collect_form_job,
    fill_xg_job,
    calculate_accuracy_job,
    backup_database_job,
)

from monitor.scheduler.match_ops import (
    lock_predictions_job,
    match_monitor_job,
    relock_finished_job,
)

from monitor.scheduler.training import (
    daily_audit_wrapper,
    weekly_audit_wrapper,
    self_heal_wrapper,
    core_nn_train_wrapper,
    draw_classifier_train_wrapper,
    halftime_train_wrapper,
    score_train_wrapper,
    handicap_train_wrapper,
    fusion_train_wrapper,
    data_quality_wrapper,
    jingcai_sync_job,
    auto_verify_wrapper,
    auto_learn_wrapper,
    param_optimize_wrapper,
    nn_retrain_monitor_wrapper,
    prediction_snapshot_wrapper,
)

from monitor.scheduler.health import (
    health_check_wrapper,
    strategy_monitor_wrapper,
    injury_sync_wrapper,
    zgzcw_sync_wrapper,
)


def stop_scheduler():
    """停止所有定时任务"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("[scheduler] All jobs stopped")
    else:
        logger.info("[scheduler] Not running, skip shutdown")


@scheduler.scheduled_job('interval', id='heartbeat', minutes=5)
def heartbeat():
    """心跳检测，记录调度器存活状态"""
    logger.info("[scheduler] Heartbeat OK")


def start_scheduler():
    """启动所有定时任务"""

    # ── 数据采集任务 ──
    scheduler.add_job(
        collect_zgzcw_job,
        trigger=IntervalTrigger(minutes=30),
        id="collect_zgzcw",
        name="Zgzcw Odds Collection (37 companies, free, CN)",
        replace_existing=True
    )

    scheduler.add_job(
        collect_500_job,
        trigger=IntervalTrigger(minutes=30),
        id="collect_500",
        name="500.com Odds Collection (20+ companies, free, CN)",
        replace_existing=True
    )

    scheduler.add_job(
        collect_odds_tier1_job,
        trigger=IntervalTrigger(hours=2),
        id="collect_odds_tier1",
        name="Odds Collection Tier 1 (Primary)",
        replace_existing=True
    )

    scheduler.add_job(
        collect_odds_tier2_job,
        trigger=CronTrigger(hour="8,20", minute=0),
        id="collect_odds_tier2",
        name="Odds Collection Tier 2 (Premium)",
        replace_existing=True
    )

    scheduler.add_job(
        collect_odds_tier3_job,
        trigger=CronTrigger(hour=12, minute=0),
        id="collect_odds_tier3",
        name="Odds Collection Tier 3 (Focus)",
        replace_existing=True
    )

    scheduler.add_job(
        auto_focus_trigger,
        trigger=IntervalTrigger(hours=1),
        id="auto_focus_trigger",
        name="Auto Focus Trigger (4h pre-match)",
        replace_existing=True
    )

    scheduler.add_job(
        collect_closing_odds_job,
        trigger=IntervalTrigger(minutes=15),
        id="collect_closing_odds",
        name="Closing Odds Collection (real market only)",
        replace_existing=True
    )

    scheduler.add_job(
        update_opening_odds_job,
        trigger=IntervalTrigger(hours=6),
        id="update_opening_odds",
        name="Opening Odds Update (batch)",
        replace_existing=True
    )

    scheduler.add_job(
        live_odds_poll_job,
        trigger=IntervalTrigger(minutes=5),
        id="live_odds_poll",
        name="Live Odds Polling",
        replace_existing=True
    )

    # ── 比赛预测与监控 ──
    scheduler.add_job(
        lock_predictions_job,
        trigger=IntervalTrigger(hours=1),
        id="lock_predictions",
        name="Prediction Lock",
        replace_existing=True
    )

    scheduler.add_job(
        match_monitor_job,
        trigger=IntervalTrigger(minutes=1),
        id="match_monitor",
        name="Match Monitor",
        replace_existing=True
    )

    # ── 结果同步 ──
    scheduler.add_job(
        sync_results_job,
        trigger=IntervalTrigger(minutes=5),
        id="sync_results",
        name="Result Sync",
        replace_existing=True
    )

    scheduler.add_job(
        zgzcw_draw_sync_job,
        trigger=IntervalTrigger(hours=6),
        id="zgzcw_draw_sync",
        name="Zgzcw Draw Result Sync (6h)",
        replace_existing=True
    )

    scheduler.add_job(
        fill_drawn_issues_job,
        trigger=IntervalTrigger(hours=6),
        id="fill_drawn_issues",
        name="Jingcai Draw Result Auto-Fill",
        replace_existing=True
    )

    # ── 外部数据同步 ──
    scheduler.add_job(
        collect_fbref_stats_job,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="collect_fbref_stats",
        name="FBref Team & Player Stats Sync",
        replace_existing=True
    )

    scheduler.add_job(
        collect_elo_ratings_job,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=30),
        id="collect_elo_ratings",
        name="Club Elo Ratings Sync",
        replace_existing=True
    )

    scheduler.add_job(
        collect_form_job,
        trigger=CronTrigger(hour=6, minute=0),
        id="collect_form",
        name="Team Form Collection (football-data.org)",
        replace_existing=True
    )

    scheduler.add_job(
        fill_xg_job,
        trigger=CronTrigger(hour=5, minute=0),
        id="fill_xg",
        name="xG Estimation (Elo regression)",
        replace_existing=True
    )

    scheduler.add_job(
        calculate_accuracy_job,
        trigger=IntervalTrigger(hours=1),
        id="calc_accuracy",
        name="Accuracy Calculation",
        replace_existing=True
    )

    # ── 竞彩 ──
    scheduler.add_job(
        jingcai_sync_job,
        trigger=CronTrigger(hour="9,15", minute=0),
        id="jingcai_sync",
        name="Jingcai Issue Sync (sporttery API)",
        replace_existing=True
    )

    scheduler.add_job(
        jingcai_realtime_results_wrapper,
        trigger=IntervalTrigger(minutes=2),
        id="jingcai_realtime_results",
        name="Jingcai Realtime Results (2min)",
        replace_existing=True
    )

    scheduler.add_job(
        jingcai_auto_verify_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="jingcai_auto_verify",
        name="Jingcai Auto-Verify (1h)",
        replace_existing=True
    )

    # ── 备份 ──
    scheduler.add_job(
        backup_database_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="backup_db",
        name="Database Backup",
        replace_existing=True
    )

    # ── 健康监控 ──
    scheduler.add_job(
        health_check_wrapper,
        trigger=IntervalTrigger(minutes=10),
        id="health_check",
        name="Health Daemon (self-check + self-repair)",
        replace_existing=True,
    )

    scheduler.add_job(
        strategy_monitor_wrapper,
        trigger=CronTrigger(hour=22, minute=0),
        id="strategy_monitor",
        name="Strategy Drift Monitor (daily)",
        replace_existing=True
    )

    scheduler.add_job(
        injury_sync_wrapper,
        trigger=CronTrigger(hour=8, minute=0),
        id="injury_sync",
        name="Injury/Suspension Data Sync (daily)",
        replace_existing=True
    )

    scheduler.add_job(
        zgzcw_sync_wrapper,
        trigger=IntervalTrigger(minutes=30),
        id="zgzcw_sync",
        name="Zgzcw JC Sync (consolidated: replaced 3 redundant tasks)",
        replace_existing=True
    )

    # ── 模型审计与训练 ──
    scheduler.add_job(
        daily_audit_wrapper,
        trigger=CronTrigger(hour=5, minute=30),
        id="daily_audit",
        name="Model Daily Audit (prediction vs result)",
        replace_existing=True,
    )

    scheduler.add_job(
        weekly_audit_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="weekly_audit",
        name="Model Weekly Deep Audit + Self-Heal",
        replace_existing=True,
    )

    scheduler.add_job(
        self_heal_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=15),
        id="self_heal_cycle",
        name="Self-Heal Cycle: Audit -> Reweight -> Regenerate",
        replace_existing=True,
    )

    scheduler.add_job(
        data_quality_wrapper,
        trigger=CronTrigger(hour=5, minute=45),
        id="data_quality_check",
        name="Daily Data Quality Check + Auto-Fix",
        replace_existing=True,
    )

    scheduler.add_job(
        fusion_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=5),
        id="fusion_train_weekly",
        name="Fusion LR Training (Global + Leagues + Knockout)",
        replace_existing=True,
    )

    scheduler.add_job(
        core_nn_train_wrapper,
        trigger=CronTrigger(hour=4, minute=0),
        id="core_nn_train",
        name="Core Stacking NN Training (Daily)",
        replace_existing=True,
    )

    scheduler.add_job(
        draw_classifier_train_wrapper,
        trigger=CronTrigger(hour=6, minute=35),
        id="draw_classifier_train",
        name="Draw Classifier Daily Training",
        replace_existing=True,
    )

    scheduler.add_job(
        halftime_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=45),
        id="halftime_train",
        name="Halftime Sub-model Weekly Training",
        replace_existing=True,
    )

    scheduler.add_job(
        score_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=50),
        id="score_train",
        name="Score Sub-model Weekly Training",
        replace_existing=True,
    )

    scheduler.add_job(
        handicap_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=55),
        id="handicap_train",
        name="Handicap Sub-model Weekly Training",
        replace_existing=True,
    )

    scheduler.add_job(
        auto_verify_wrapper,
        trigger=IntervalTrigger(hours=6),
        id="auto_verify_jingcai",
        name="Jingcai Auto-Verify (6h)",
        replace_existing=True,
    )

    scheduler.add_job(
        auto_learn_wrapper,
        trigger=IntervalTrigger(hours=6),
        id="auto_learn_nn",
        name="Auto NN Incremental Training (6h)",
        replace_existing=True,
    )

    scheduler.add_job(
        relock_finished_job,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="relock_finished",
        name="Re-lock Finished Match Predictions (with DrawDetection)",
        replace_existing=True,
    )

    scheduler.add_job(
        param_optimize_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=30, week="1-51/2"),
        id="param_optimize",
        name="Strategy Parameter Auto-Optimization (biweekly)",
        replace_existing=True,
    )

    scheduler.add_job(
        nn_retrain_monitor_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=15),
        id="nn_retrain_monitor",
        name="NN Retrain Strategy Callback (weekly)",
        replace_existing=True,
    )

    scheduler.add_job(
        prediction_snapshot_wrapper,
        trigger=CronTrigger(minute="*/30"),
        id="prediction_snapshot_job",
        name="Prediction Snapshot Generation (Upcoming 2h)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[scheduler] All jobs started")


__all__ = [
    "scheduler",
    "start_scheduler",
    "stop_scheduler",
    "heartbeat",
    "DBSession",
    "cleanup_old_backups",
    # All job functions
    "collect_zgzcw_job",
    "collect_500_job",
    "collect_odds_tier1_job",
    "collect_odds_tier2_job",
    "collect_odds_tier3_job",
    "collect_closing_odds_job",
    "update_opening_odds_job",
    "live_odds_poll_job",
    "auto_focus_trigger",
    "lock_predictions_job",
    "match_monitor_job",
    "relock_finished_job",
    "sync_results_job",
    "zgzcw_draw_sync_job",
    "fill_drawn_issues_job",
    "jingcai_auto_verify_wrapper",
    "jingcai_realtime_results_wrapper",
    "backup_database_job",
    "collect_fbref_stats_job",
    "collect_elo_ratings_job",
    "collect_form_job",
    "fill_xg_job",
    "calculate_accuracy_job",
    "health_check_wrapper",
    "daily_audit_wrapper",
    "weekly_audit_wrapper",
    "self_heal_wrapper",
    "data_quality_wrapper",
    "fusion_train_wrapper",
    "core_nn_train_wrapper",
    "draw_classifier_train_wrapper",
    "halftime_train_wrapper",
    "score_train_wrapper",
    "handicap_train_wrapper",
    "jingcai_sync_job",
    "auto_verify_wrapper",
    "auto_learn_wrapper",
    "param_optimize_wrapper",
    "nn_retrain_monitor_wrapper",
    "prediction_snapshot_wrapper",
    "strategy_monitor_wrapper",
    "injury_sync_wrapper",
    "zgzcw_sync_wrapper",
]
