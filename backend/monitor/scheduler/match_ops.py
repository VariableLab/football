"""
比赛预测锁定、状态监控、重算相关任务
"""
import json
from datetime import datetime, timedelta, timezone

from database.models import Match, MatchStatus, Prediction
from utils.logger import get_logger

logger = get_logger("scheduler.match_ops")


def _convert_numpy(obj):
    """递归将 numpy 类型转换为原生 Python 类型，避免 JSON 序列化失败"""
    if isinstance(obj, dict):
        return {k: _convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_convert_numpy(v) for v in obj]
    # numpy scalar 检测 — 用 .item() 转为原生 Python 类型
    typ = type(obj)
    mod = getattr(typ, '__module__', '')
    if mod == "numpy" or mod.startswith("numpy."):
        if hasattr(obj, "item"):
            return obj.item()
        return float(obj)
    return obj


def lock_predictions_job():
    """对48h内即将开始的比赛，自动运行预测模型并锁定快照。"""
    now = datetime.now(timezone.utc)
    window = now + timedelta(hours=48)

    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        matches = db.query(Match).filter(
            Match.kickoff_at <= window,
            Match.kickoff_at > now,
            Match.status == MatchStatus.SCHEDULED
        ).all()

        new_predictions = []
        matches_to_update = []

        for match in matches:
            existing = db.query(Prediction).filter(Prediction.match_id == match.id).first()
            if existing:
                continue

            try:
                from core.prediction_engine import PredictionEngine, build_context_from_match
                ctx = build_context_from_match(match)
                engine = PredictionEngine(db_session=db)
                result = engine.predict(ctx)

                for payload in result.to_db_payload():
                    new_predictions.append((
                        match.id,
                        payload["play_type"],
                        _convert_numpy(payload["probabilities"]),
                        payload.get("confidence"),
                        payload.get("model_version", "v2.0_linear"),
                    ))

                logger.info(
                    f"[prediction] {match.match_code} ready | "
                    f"SPF: H={result.spf.get('home', 0):.2%} D={result.spf.get('draw', 0):.2%} A={result.spf.get('away', 0):.2%}"
                )
            except Exception as e:
                logger.error(f"[prediction] Failed to lock {match.match_code}: {e}")
                continue

            matches_to_update.append(match)

        if new_predictions:
            db.add_all([
                Prediction(
                    match_id=mid,
                    play_type=ptype,
                    probabilities=probs,
                    confidence=conf,
                    model_version=ver,
                )
                for mid, ptype, probs, conf, ver in new_predictions
            ])
            logger.info(f"[prediction] Batch inserted {len(new_predictions)} predictions")

        for match in matches_to_update:
            match.status = MatchStatus.UPCOMING

        db.commit()


def match_monitor_job():
    """检查是否有比赛已开始或已结束。每分钟运行。"""
    now = datetime.now(timezone.utc)
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        starting = db.query(Match).filter(
            Match.kickoff_at <= now + timedelta(minutes=5),
            Match.kickoff_at > now - timedelta(minutes=5),
            Match.status == MatchStatus.UPCOMING
        ).all()

        for match in starting:
            logger.info(f"[monitor] Match {match.match_code} is starting")
            match.status = MatchStatus.LIVE
            db.commit()

        ended = db.query(Match).filter(
            Match.status == MatchStatus.LIVE,
            Match.kickoff_at < now - timedelta(minutes=105)
        ).all()

        for match in ended:
            logger.debug(f"[monitor] Match {match.match_code} likely ended, awaiting result input")


def relock_finished_job():
    """对已结束且有结果的比赛重新运行预测引擎，更新概率（含DrawDetection）。每周一 07:00"""
    from monitor.scheduler.jobs import DBSession
    from core.prediction_engine import PredictionEngine, build_context_from_match

    with DBSession() as db:
        matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
            Match.closing_odds_home != None,
            Match.closing_odds_home > 1.01,
        ).limit(500).all()

        updated = 0
        skipped = 0
        for match in matches:
            try:
                existing_locked = db.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.locked_at.isnot(None),
                ).first()
                if existing_locked:
                    skipped += 1
                    continue

                ctx = build_context_from_match(match)
                engine = PredictionEngine(db_session=db)
                result = engine.predict(ctx)
                for payload in result.to_db_payload():
                    pred = db.query(Prediction).filter(
                        Prediction.match_id == match.id,
                        Prediction.play_type == payload["play_type"],
                    ).first()
                    probs = _convert_numpy(payload["probabilities"])
                    if pred:
                        pred.probabilities = probs
                        pred.confidence = payload.get("confidence")
                        pred.model_version = payload.get("model_version", "v1.0")
                    else:
                        pred = Prediction(
                            match_id=match.id,
                            play_type=payload["play_type"],
                            probabilities=probs,
                            confidence=payload.get("confidence"),
                            model_version=payload.get("model_version", "v1.0"),
                        )
                        db.add(pred)
                updated += 1
            except Exception as e:
                logger.debug(f"[relock] Skip {match.match_code}: {e}")

        db.commit()
        logger.info(f"[relock] Updated {updated}, skipped {skipped} (already locked) / {len(matches)} finished matches")
