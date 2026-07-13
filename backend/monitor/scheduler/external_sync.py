"""
外部数据同步任务 — 球队统计、ELO、状态、xG、准确率
"""
from datetime import datetime, timedelta, timezone

from database.models import Match, MatchStatus, Prediction
from utils.logger import get_logger

logger = get_logger("scheduler.external_sync")


def collect_fbref_stats_job():
    """每周同步 FBref 高级统计到数据库。"""
    try:
        from integrations.soccerdata_adapter import SoccerDataSync
        from monitor.scheduler.jobs import DBSession
        with DBSession() as db:
            sync = SoccerDataSync(db)
            team_cnt = sync.sync_fbref_team_stats("INT-World Cup", "2022")
            player_cnt = sync.sync_fbref_player_stats("INT-World Cup", "2022")
            logger.info(f"[fbref-sync] Weekly sync done: teams={team_cnt}, players={player_cnt}")
    except Exception as e:
        logger.error(f"[fbref-sync] Weekly sync failed: {e}")


def collect_elo_ratings_job():
    """每周同步 Club Elo 等级分到 teams 表。"""
    try:
        from integrations.soccerdata_adapter import SoccerDataSync
        from monitor.scheduler.jobs import DBSession
        with DBSession() as db:
            sync = SoccerDataSync(db)
            updated = sync.sync_elo_ratings()
            logger.info(f"[elo-sync] Weekly sync done: updated={updated}")
    except Exception as e:
        logger.error(f"[elo-sync] Weekly sync failed: {e}")


def collect_form_job():
    """每天自动刷新所有球队的近期战绩。"""
    from scripts.form_collector import FormCollector
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        try:
            collector = FormCollector(db)
            stats = collector.refresh_all(use_external=True)
            logger.info(
                f"[form] Daily refresh done: updated={stats['updated']}, "
                f"skipped={stats['skipped']}, failed={stats['failed']}"
            )
        except Exception as e:
            logger.error(f"[form] Daily refresh failed: {e}")


def fill_xg_job():
    """为缺失 xG/xGA 的球队填充估算值"""
    from monitor.scheduler.jobs import DBSession
    from scripts.xg_estimator import fill_missing_xg

    with DBSession() as db:
        try:
            stats = fill_missing_xg(db)
            logger.info(
                f"[xg-fill] xG estimation done: skipped={stats['skipped']}, "
                f"from_matches={stats['from_matches']}, from_elo={stats['from_elo']}, "
                f"from_default={stats['from_default']}, errors={stats['errors']}"
            )
        except Exception as e:
            logger.error(f"[xg-fill] xG estimation failed: {e}")


def calculate_accuracy_job():
    """对已结束且已录入预测的比赛，自动计算各玩法准确率"""
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        finished = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None)
        ).all()

        total_checked = 0
        correct = 0

        for match in finished:
            preds = db.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == "SPF"
            ).all()

            for pred in preds:
                total_checked += 1
                probs = pred.probabilities
                predicted = max(probs, key=probs.get)
                if predicted == match.actual_outcome:
                    correct += 1

        if total_checked > 0:
            accuracy = correct / total_checked
            logger.info(f"[accuracy] SPF accuracy: {accuracy:.2%} ({correct}/{total_checked})")


def backup_database_job(backup_dir=None, db_path=None, keep_daily=7, keep_weekly=4, max_size_gb=5.0):
    """每日备份 SQLite 数据库到 backup/ 目录。"""
    import os
    import sqlite3
    import hashlib
    from monitor.scheduler.jobs import _BACKEND_ROOT

    if backup_dir is None:
        try:
            from database.config import get_settings
            settings = get_settings()
        except Exception:
            settings = None
            _BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if db_path is None:
            if settings and settings.DATABASE_URL.startswith("sqlite:///"):
                db_path = settings.DATABASE_URL.replace("sqlite:///", "")
            else:
                db_path = os.path.join(_BACKEND_ROOT, "database.sqlite")
        else:
            if not os.path.isabs(db_path):
                abs_candidate = os.path.abspath(os.path.join(_BACKEND_ROOT, db_path.replace("./", "")))
                if os.path.exists(abs_candidate) or not os.path.exists(db_path):
                    db_path = abs_candidate

    if backup_dir is None:
        backup_dir = os.path.join(_BACKEND_ROOT, "backup")
    else:
        if not os.path.isabs(backup_dir):
            backup_dir = os.path.abspath(os.path.join(_BACKEND_ROOT, backup_dir.replace("./", "")))

    os.makedirs(backup_dir, exist_ok=True)

    if not os.path.exists(db_path):
        logger.warning(f"[backup] DB not found, skip: {db_path}")
        return {"status": "skipped", "reason": "db_missing"}

    try:
        with open(db_path, "rb") as f:
            db_hash = hashlib.md5(f.read()).hexdigest()[:12]
    except OSError as e:
        logger.error(f"[backup] Failed to hash db: {e}")
        return {"status": "failed", "reason": str(e)}

    meta_path = os.path.join(backup_dir, ".backup_meta.json")
    last_hash = None
    if os.path.exists(meta_path):
        try:
            import json
            with open(meta_path, "r") as f:
                meta = json.load(f)
                last_hash = meta.get("last_hash")
        except Exception:
            pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"db_{timestamp}.sqlite")

    if db_hash == last_hash:
        logger.info(f"[backup] DB unchanged (hash={db_hash}), skip")
        return {"status": "skipped", "reason": "unchanged", "hash": db_hash}

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        size_mb = os.path.getsize(backup_path) / 1024 / 1024
        logger.info(f"[backup] OK -> {backup_path} ({size_mb:.1f}MB, hash={db_hash})")
    except Exception as e:
        logger.error(f"[backup] Backup failed: {e}")
        if os.path.exists(backup_path):
            os.remove(backup_path)
        return {"status": "failed", "reason": str(e)}
    finally:
        src.close()
        dst.close()

    try:
        import json
        with open(meta_path, "w") as f:
            json.dump({
                "last_hash": db_hash,
                "last_backup": timestamp,
                "last_size_mb": round(size_mb, 2),
            }, f)
    except OSError:
        pass

    from monitor.scheduler.jobs import cleanup_old_backups
    cleanup_old_backups(backup_dir, keep_daily, keep_weekly, max_size_gb)
    return {"status": "ok", "path": backup_path, "size_mb": round(size_mb, 2)}
