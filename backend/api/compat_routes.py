"""
API 兼容路由 — 6-16 动态审计发现的 11 个 404 端点修复
========================================================

修复日期: 2026-06-17
原因: docs/audits/2026-06-16/AUDIT_DYNAMIC_20260616.md 报告 11 个端点返回 404,
      但其中大部分对应的"业务能力"在其它路由下存在 —— 只是路径不同。

策略: 用 APIRoute 包装,挂到 /api/ 下面,提供"原名 → 实际路由"的桥接。
      不破坏现有路由,只是给老客户端一个保底入口。

修复清单:
  1. GET  /api/strategy/optimal-combo       → /api/jingcai/issues/{id}/optimal-combo
  2. GET  /api/strategy/tiered              → /api/advisor/top-picks
  3. GET  /api/strategy/picks/{match_id}    → /api/matches/{match_id}/strategy
  4. GET  /api/strategy/picks               → /api/advisor/top-picks
  5. GET  /api/odds/movements               → /api/matches/{match_id}/odds-movement
  6. GET  /api/odds/movements/{match_id}    → /api/matches/{match_id}/odds-movement
  7. GET  /api/predictions                  → /api/matches?status=upcoming
  8. GET  /api/predictions/{match_id}       → /api/matches/{match_id}
  9. GET  /api/live-odds                    → /api/live/live-odds
  10. GET /api/live-odds/{match_id}         → /api/live/live-odds/{match_id}
  11. GET /api/validation/report            → /api/validation
  12. GET /api/validation/summary           → /api/validation/by-play-type
  13. GET /api/validation/recent            → /api/validation/calibration
  14. GET /api/admin/dashboard              → /api/admin/data-quality
  15. GET /api/monitor/health               → /api/health (重命名后)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 这个 router 不带 prefix,所有路径写全称
router = APIRouter(tags=["compat"], include_in_schema=False)


def _get_db(request: Request):
    """延迟获取 db session,避免在 router 加载时就连数据库。"""
    from database.models import get_db
    yield from get_db()


# ──────────────────────────────────────────────────────
# 1-4. /api/strategy/* 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/strategy/optimal-combo")
def compat_optimal_combo(issue_id: int = 1, top_n: int = 8, db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/jingcai/issues/{id}/optimal-combo"""
    try:
        from optimal_combo import compute_optimal_combo
        picks = compute_optimal_combo(db, issue_id, top_n)
        return {
            "issue_id": issue_id,
            "picks": picks,
            "total": len(picks),
            "_compat": {"from": "/api/strategy/optimal-combo", "new": f"/api/jingcai/issues/{issue_id}/optimal-combo"},
        }
    except Exception as e:
        logger.warning(f"[compat] optimal-combo failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


@router.get("/api/strategy/tiered")
def compat_tiered(db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/advisor/top-picks"""
    try:
        from api.routers.advisor import get_top_picks
        return get_top_picks(db=db)
    except Exception as e:
        logger.warning(f"[compat] tiered failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


@router.get("/api/strategy/picks")
def compat_picks_all(db: Session = Depends(_get_db)):
    """兼容老路径(无 match_id) → 实际: /api/advisor/top-picks"""
    return compat_tiered(db=db)


@router.get("/api/strategy/picks/{match_id}")
def compat_picks_match(match_id: int, db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/matches/{id}/strategy"""
    try:
        from api.routers.matches import get_match_strategy
        return get_match_strategy(match_id=match_id, db=db)
    except Exception as e:
        logger.warning(f"[compat] picks/{match_id} failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


# ──────────────────────────────────────────────────────
# 5-6. /api/odds/movements 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/odds/movements")
def compat_odds_movements_all(db: Session = Depends(_get_db)):
    """不带 match_id → 返回所有最近 24h 异动(用最近一场比赛代理)"""
    from database.models import Match, MatchStatus
    from datetime import datetime, timedelta

    try:
        # 取最近一场 upcoming match
        m = (
            db.query(Match)
            .filter(Match.kickoff >= datetime.utcnow() - timedelta(days=1))
            .filter(Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.LIVE]))
            .order_by(Match.kickoff.asc())
            .first()
        )
        if not m:
            return {"match_id": None, "movements": [], "total": 0,
                    "_compat": "no_upcoming_matches"}
        return compat_odds_movements_match(m.id, db)
    except Exception as e:
        logger.warning(f"[compat] odds/movements failed: {e}")
        return {"match_id": None, "movements": [], "total": 0, "_error": str(e)}


@router.get("/api/odds/movements/{match_id}")
def compat_odds_movements_match(match_id: int, db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/matches/{id}/odds-movement"""
    try:
        from api.routers.matches import get_match_odds_movement
        return get_match_odds_movement(match_id=match_id, db=db)
    except Exception as e:
        logger.warning(f"[compat] odds/movements/{match_id} failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


# ──────────────────────────────────────────────────────
# 7-8. /api/predictions 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/predictions")
def compat_predictions_all(status: str = "upcoming", limit: int = 10, db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/matches?status=upcoming"""
    try:
        from api.routers.matches import list_matches
        return list_matches(status=status, limit=limit, db=db)
    except Exception as e:
        logger.warning(f"[compat] predictions failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


@router.get("/api/predictions/{match_id}")
def compat_predictions_match(match_id: int, db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/matches/{id}"""
    try:
        from api.routers.matches import get_match
        return get_match(match_id=match_id, db=db)
    except Exception as e:
        logger.warning(f"[compat] predictions/{match_id} failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


# ──────────────────────────────────────────────────────
# 9-10. /api/live-odds 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/live-odds")
def compat_live_odds_all():
    """兼容老路径 → 实际: /api/live/live-odds"""
    return JSONResponse(
        status_code=307,
        content={"detail": "Moved permanently", "new_path": "/api/live/live-odds"},
        headers={"Location": "/api/live/live-odds"},
    )


@router.get("/api/live-odds/{match_id}")
def compat_live_odds_match(match_id: int):
    """兼容老路径 → 实际: /api/live/live-odds/{id}"""
    return JSONResponse(
        status_code=307,
        content={"detail": "Moved permanently", "new_path": f"/api/live/live-odds/{match_id}"},
        headers={"Location": f"/api/live/live-odds/{match_id}"},
    )


# ──────────────────────────────────────────────────────
# 11-13. /api/validation/* 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/validation/report")
def compat_validation_report(db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/validation"""
    try:
        from api.routers.validation import public_validation
        return public_validation(db=db)
    except Exception as e:
        logger.warning(f"[compat] validation/report failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


@router.get("/api/validation/summary")
def compat_validation_summary(db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/validation/by-play-type"""
    try:
        from api.routers.validation import validation_by_play_type
        return validation_by_play_type(db=db)
    except Exception as e:
        logger.warning(f"[compat] validation/summary failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


@router.get("/api/validation/recent")
def compat_validation_recent(db: Session = Depends(_get_db)):
    """兼容老路径 → 实际: /api/validation/calibration"""
    try:
        from api.routers.validation import calibration_curve
        return calibration_curve(db=db)
    except Exception as e:
        logger.warning(f"[compat] validation/recent failed: {e}")
        raise HTTPException(status_code=503, detail=f"compat_unavailable: {e}")


# ──────────────────────────────────────────────────────
# 14. /api/admin/dashboard 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/admin/dashboard")
def compat_admin_dashboard():
    """兼容老路径 → 实际: /api/admin/data-quality"""
    return JSONResponse(
        status_code=307,
        content={"detail": "Moved permanently", "new_path": "/api/admin/data-quality"},
        headers={"Location": "/api/admin/data-quality"},
    )


# ──────────────────────────────────────────────────────
# 15. /api/monitor/health 兼容
# ──────────────────────────────────────────────────────
@router.get("/api/monitor/health")
def compat_monitor_health():
    """兼容老路径 → 实际: /api/health"""
    return JSONResponse(
        status_code=307,
        content={"detail": "Moved permanently", "new_path": "/api/health"},
        headers={"Location": "/api/health"},
    )
