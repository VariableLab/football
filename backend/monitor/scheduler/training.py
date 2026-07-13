"""
模型训练、审计、自修相关任务
"""
from utils.logger import get_logger

logger = get_logger("scheduler.training")


def daily_audit_wrapper():
    """模型每日复盘：每天 05:30"""
    from monitor.model_audit import daily_audit_job
    daily_audit_job()


def weekly_audit_wrapper():
    """模型每周深度复盘：每周一 06:00"""
    from monitor.model_audit import weekly_audit_job
    weekly_audit_job()


def self_heal_wrapper():
    """自愈闭环：审计→重学→重生成：每周一 06:15"""
    from monitor.model_audit import self_heal_job
    result = self_heal_job()
    status = result.get("status", "unknown")
    dur = result.get("duration_seconds", 0)
    logger.info(f"[self-heal-scheduled] status={status}, duration={dur:.0f}s")


def core_nn_train_wrapper():
    """预测神经网络每日训练：每天 06:30"""
    from core.residual_nn import StackingTrainer
    from database.models import SessionLocal

    with SessionLocal() as db:
        trainer = StackingTrainer(db_session=db)
        result = trainer.train()
        logger.info(f"[MLOps] Stacking NN Training result: {result}")


def draw_classifier_train_wrapper():
    """平局分类器每日训练：每天 06:35"""
    from core.draw_classifier import draw_classifier_train_job
    draw_classifier_train_job()


def halftime_train_wrapper():
    """半场子模型每周增量训练：每周一 06:45"""
    from scripts.sub_model_halftime import halftime_train_job
    halftime_train_job()


def score_train_wrapper():
    """比分子模型每周增量训练：每周一 06:50"""
    from scripts.sub_model_score import score_train_job
    score_train_job()


def handicap_train_wrapper():
    """让球子模型每周增量训练：每周一 06:55"""
    from scripts.sub_model_handicap import handicap_train_job
    handicap_train_job()


def fusion_train_wrapper():
    """Fusion 逻辑回归训练：每周一 06:05（含 A/B 验证部署）"""
    from fusion.validate_deploy import train_with_validation

    result = train_with_validation(
        l1_penalty=0.001,
        class_weight={0: 0.8, 1: 1.5, 2: 0.8},
        val_ratio=0.1,
    )
    if result.get("deployed"):
        logger.info(f"[fusion-train] Deployed new weights (delta_brier={result.get('delta_brier', 'N/A')})")
    elif result.get("decision") == "keep_old":
        logger.warning(f"[fusion-train] New weights rejected (delta_brier={result.get('delta_brier', 'N/A')})")
    else:
        logger.warning(f"[fusion-train] Deployment skipped: {result.get('decision', 'unknown')}")


def data_quality_wrapper():
    """每日数据质量检查 + 自动修复：每天 05:45"""
    from ingestion.data_cleaner import DataCleaner
    from database.config import get_db

    db = next(get_db())
    try:
        cleaner = DataCleaner(db)
        findings = cleaner.audit()
        critical = [f for f in findings if f.severity == "critical"]
        if critical:
            logger.warning(f"[data-quality] {len(critical)} critical issues found")
            for f in critical:
                logger.warning(f"  - {f.category}: {f.description}")
            result = cleaner.clean(dry_run=False)
            fixed = {k: v for k, v in result.fixed.items() if v > 0}
            if fixed:
                logger.info(f"[data-quality] Auto-fixed: {fixed}")
        else:
            logger.info(f"[data-quality] {len(findings)} issues found, none critical")
    except Exception as e:
        logger.error(f"[data-quality] Error: {e}")
    finally:
        db.close()


def jingcai_sync_job():
    """竞彩期号同步：每天 09:00, 15:00"""
    from core.jingcai_predictor import cmd_issue_sync
    try:
        cmd_issue_sync(days=3)
        logger.info("[jingcai-sync] Daily sync done")
    except Exception as e:
        logger.error(f"[jingcai-sync] Daily sync failed: {e}")
        from monitor.alert_manager import fire_alert
        fire_alert("jingcai_sync", "critical", f"竞彩期号同步失败: {e}")


def auto_verify_wrapper():
    """竞彩自动核验（drawn → verification）：每 6 小时"""
    from monitor.auto_learner import auto_verify_jingcai
    auto_verify_jingcai()


def auto_learn_wrapper():
    """增量 NN 重训练（有新结果时触发）：每 6 小时"""
    from monitor.auto_learner import auto_learn_trigger
    result = auto_learn_trigger()
    if result.get("triggered"):
        logger.info(
            f"[auto-learn] Triggered: {result['new_results']} new results, "
            f"trained: {result['trained']}"
        )


def param_optimize_wrapper():
    """参数自动寻优：每两周 周一 07:30"""
    from scripts.param_optimizer import param_optimize_job
    param_optimize_job()


def nn_retrain_monitor_wrapper():
    """NN 重训练回调：每周一 07:15"""
    from monitor.strategy_monitor import nn_retrain_callback
    result = nn_retrain_callback()
    logger.info(f"[nn-retrain-monitor] {result['action']}: {result['next_step']}")


def prediction_snapshot_wrapper():
    """预测快照生成：每 30 分钟检查未来 2 小时比赛"""
    from database.models import SessionLocal
    from core.prediction_snapshot import PredictionSnapshotManager

    with SessionLocal() as db:
        mgr = PredictionSnapshotManager(db)
        count = mgr.generate_for_upcoming(hours=2)
        if count > 0:
            logger.info(f"[snapshot-job] Generated {count} snapshots")
