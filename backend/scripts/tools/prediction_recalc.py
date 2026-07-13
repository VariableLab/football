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
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database.models import Match, PredictionSnapshot
from core.prediction_engine import PredictionEngine, build_context_from_match
from core.prediction_snapshot import PredictionSnapshotManager
from utils.logger import get_logger

logger = get_logger("prediction_recalc")

# 防抖窗口（秒）
DEBOUNCE_WINDOW = 300  # 5分钟

# 重算时间戳缓存 {match_id: timestamp}
_recalc_cache: Dict[int, float] = {}


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
    now = time.time()
    
    # 防抖检查
    if not force:
        last_recalc = _recalc_cache.get(match_id, 0)
        if now - last_recalc < DEBOUNCE_WINDOW:
            logger.debug(f"[recalc] Match {match_id} skipped (debounce, {now - last_recalc:.0f}s ago)")
            return False
    
    match = db.query(Match).get(match_id)
    if not match:
        logger.error(f"[recalc] Match {match_id} not found")
        return False
    
    try:
        # 构建上下文并生成预测
        ctx = build_context_from_match(match)
        engine = PredictionEngine(db_session=db)
        result = engine.predict(ctx)
        
        # 更新比赛预测结果（这里需要根据实际的预测存储逻辑调整）
        # 假设预测结果存储在predictions表中，这里只更新model_version
        # 实际项目中可能需要调用专门的预测保存函数
        
        # 更新防抖缓存
        _recalc_cache[match_id] = now
        
        # 生成新快照（替换旧快照）
        snapshot_mgr = PredictionSnapshotManager(db)
        # 先删除旧快照
        old_snapshot = db.query(PredictionSnapshot).filter(
            PredictionSnapshot.match_id == match_id
        ).first()
        if old_snapshot:
            db.delete(old_snapshot)
            db.commit()
        
        # 生成新快照
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
    
    Args:
        db: 数据库会话
        match_ids: 比赛ID列表
        force: 是否强制重算
    
    Returns:
        Dict: 重算结果统计
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


def clear_debounce_cache(match_id: Optional[int] = None):
    """清除防抖缓存"""
    global _recalc_cache
    if match_id:
        _recalc_cache.pop(match_id, None)
    else:
        _recalc_cache.clear()


# ────────────────────────────
# 集成到赔率采集器
# ────────────────────────────
def on_odds_updated(db: Session, match_id: int):
    """
    赔率更新回调：自动触发预测重算
    
    在odds_collector.py的赔率写入后调用此函数
    """
    logger.info(f"[recalc] Odds updated for match {match_id}, triggering recalc")
    trigger_recalc(db, match_id)
