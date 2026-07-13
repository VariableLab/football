"""
赔率更新后预测重算触发器

功能:
- 赔率更新后自动触发预测重算
- 防抖机制：同一比赛5分钟内不重复重算
- 重算后更新model_version和快照
- 失败告警

用法:
  from core.prediction_recalc import trigger_recalc
  trigger_recalc(db, match_id)

重要修复（2026-06-25）：
  旧的 ``_recalc_cache`` 是模块级 Python dict，进程本地。
  gunicorn 多 worker 模式下每个 worker 独立计数，结果是 debounce
  几乎不起作用 —— 大量冗余重算触发 + 频繁入库，导致 DB 抖动。

  新实现把时间戳持久化到 ``debounce_entries`` 表（DB 共享），通过
  SQL upsert 原子写入 + WHERE 子句做条件防抖，跨进程一致。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import insert, update, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Match, PredictionSnapshot, SessionLocal, DebounceEntry
from core.prediction_engine import PredictionEngine, build_context_from_match
from core.prediction_snapshot import PredictionSnapshotManager
from utils.logger import get_logger

logger = get_logger("prediction_recalc")

# 防抖窗口（秒）
DEBOUNCE_WINDOW = 300  # 5 分钟

SCOPE_RECALC = "recalc"


def _debounce_key(match_id: int) -> str:
    return f"match:{match_id}"


def _should_debounce(db: Session, key: str, window_s: int, force: bool) -> bool:
    """
    当 force=True 时跳过窗口检查（重训练后强制重算的场景）。
    返回 True 表示"应该跳过"，False 表示"可以执行"。
    """
    if force:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    row = db.execute(
        select(DebounceEntry.last_executed_at).where(
            DebounceEntry.scope == SCOPE_RECALC,
            DebounceEntry.key == key,
        )
    ).first()
    if row is None:
        return False
    last_ts = row[0]
    if last_ts is None:
        return False
    # last_ts 在 DB 可能是 naive；强制视为 UTC
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    return last_ts >= cutoff


def _mark_debounce(db: Session, key: str) -> None:
    """
    原子 upsert 防抖时间戳。多 worker 同时写入场景下，靠数据库的
    ``UNIQUE(scope, key)`` 约束 + UPDATE WHERE + INSERT 兜底完成幂等。
    """
    now = datetime.now(timezone.utc)
    try:
        db.execute(
            insert(DebounceEntry).values(scope=SCOPE_RECALC, key=key, last_executed_at=now),
        )
        db.commit()
        return
    except IntegrityError:
        db.rollback()

    db.execute(
        update(DebounceEntry)
        .where(DebounceEntry.scope == SCOPE_RECALC, DebounceEntry.key == key)
        .values(last_executed_at=now)
    )
    db.commit()


def trigger_recalc(db: Session, match_id: int, force: bool = False) -> bool:
    """
    触发单场比赛预测重算

    Args:
        db: 数据库会话
        match_id: 比赛ID
        force: 是否强制重算（忽略防抖）

    Returns:
        bool: 是否成功重算
    """
    key = _debounce_key(match_id)

    if _should_debounce(db, key, DEBOUNCE_WINDOW, force):
        logger.debug(f"[recalc] Match {match_id} skipped (debounce)")
        return False

    match = db.query(Match).get(match_id)
    if not match:
        logger.error(f"[recalc] Match {match_id} not found")
        return False

    try:
        ctx = build_context_from_match(match)
        engine = PredictionEngine(db_session=db)
        result = engine.predict(ctx)

        _mark_debounce(db, key)

        snapshot_mgr = PredictionSnapshotManager(db)
        old_snapshot = db.query(PredictionSnapshot).filter(
            PredictionSnapshot.match_id == match_id
        ).first()
        if old_snapshot:
            db.delete(old_snapshot)
            db.commit()

        new_snapshot = snapshot_mgr.generate_snapshot(match_id)

        logger.info(
            f"[recalc] Match {match_id} recalculated: "
            f"version={result.model_version}, "
            f"snapshot={'created' if new_snapshot else 'failed'}"
        )
        return True

    except Exception as e:
        logger.error(f"[recalc] Match {match_id} failed: {e}")
        return False


def trigger_batch_recalc(db: Session, match_ids: list[int], force: bool = False) -> Dict:
    """
    批量触发预测重算
    """
    results = {"total": len(match_ids), "success": 0, "skipped": 0, "failed": 0}

    for match_id in match_ids:
        try:
            ok = trigger_recalc(db, match_id, force=force)
            if ok:
                results["success"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            logger.error(f"[recalc] Batch match {match_id} failed: {e}")
            results["failed"] += 1

    return results


def trigger_all_upcoming_recalc(db: Session, hours_ahead: int = 72) -> int:
    """
    触发所有即将开始比赛的预测重算（通常在模型重训后调用）
    """
    from database.models import MatchStatus
    now = datetime.now(timezone.utc)
    future_limit = now + timedelta(hours=hours_ahead)

    matches = db.query(Match).filter(
        Match.status == MatchStatus.UPCOMING,
        Match.kickoff_at > now,
        Match.kickoff_at < future_limit
    ).all()

    if not matches:
        return 0

    count = 0
    for m in matches:
        if trigger_recalc(db, m.id, force=True):
            count += 1

    return count


def clear_debounce_cache(match_id: Optional[int] = None):
    """清除防抖缓存（现在为 DB 持久化，多 worker 间一致）。"""
    s = SessionLocal()
    try:
        if match_id is None:
            s.query(DebounceEntry).filter(DebounceEntry.scope == SCOPE_RECALC).delete()
        else:
            s.query(DebounceEntry).filter(
                DebounceEntry.scope == SCOPE_RECALC,
                DebounceEntry.key == _debounce_key(match_id),
            ).delete()
        s.commit()
    finally:
        s.close()


# ────────────────────────────
# 集成到赔率采集器
# ────────────────────────────
def on_odds_updated(db: Session, match_id: int):
    """
    赔率更新回调：自动触发预测重算
    """
    logger.info(f"[recalc] Odds updated for match {match_id}, triggering recalc")
    trigger_recalc(db, match_id)
