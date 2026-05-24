from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone, timedelta
import os

from models import get_db, Match, MatchStatus, Prediction
from schemas import (
    MatchListResponse, MatchOut, StrategyResponse, 
    OddsMovementResponse, StrategyPickOut
)
from auth import get_optional_user
from strategy_pipeline import StrategyPipeline
from odds_tracker import OddsTracker

router = APIRouter(prefix="/api/matches", tags=["matches"])

_VALID_STATUSES = {"upcoming", "live", "finished", "postponed", "cancelled"}
_VALID_MATCH_TYPES = {"world_cup", "friendly", "warm_up", "qualifier"}

def _enrich_rationale(pick, match) -> str:
    """将 pipeline 的技术性 rationale 增强为用户可读的中文分析。"""
    sel_label = pick.selection_label
    parts: list[str] = []

    # 1. 核心模型估算原因
    if pick.edge > 0.05:
        parts.append(f"{sel_label}有显著价值: 模型概率{pick.model_prob_calibrated:.0%}高于市场隐含{pick.market_prob:.0%}，边际{pick.edge:+.1%}")
    elif pick.edge > 0:
        parts.append(f"{sel_label}有正期望: 模型概率{pick.model_prob_calibrated:.0%}略高于市场{pick.market_prob:.0%}，EV {pick.ev:+.1%}")
    else:
        parts.append(f"模型概率{pick.model_prob_calibrated:.0%}，赔率{pick.odds:.2f}")

    # 2. Elo 差值
    home = match.home_team
    away = match.away_team
    if home and away and home.elo and away.elo:
        elo_diff = home.elo - away.elo
        if abs(elo_diff) > 50:
            stronger = home.name if elo_diff > 0 else away.name
            parts.append(f"Elo差{elo_diff:+.0f}({stronger}占优)")
        elif abs(elo_diff) > 20:
            stronger = home.name if elo_diff > 0 else away.name
            parts.append(f"Elo差{elo_diff:+.0f}({stronger}略优)")

    # 3. 近期状态
    if home and home.form_factor and home.form_factor > 1.1:
        parts.append(f"{home.name}状态良好(系数{home.form_factor:.2f})")
    if away and away.form_factor and away.form_factor > 1.1:
        parts.append(f"{away.name}状态良好(系数{away.form_factor:.2f})")

    # 4. 置信度
    if pick.confidence == "high":
        parts.append("高置信模型估算")
    elif pick.confidence == "low":
        parts.append("低置信，谨慎参考")

    # 5. 风险提示
    if pick.risk_label in ("high", "extreme"):
        parts.append(f"风险等级{pick.risk_label}，仓位已缩减")

    return "。".join(parts)

@router.get("", response_model=MatchListResponse)
def list_matches(
    status: str = None,
    group: str = None,
    match_type: str = None,
    date: str = None,
    limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    List matches with pagination.
    Supports filtering by status (including 'jingcai'), group, match_type, date (today|tomorrow).
    """
    if status == "jingcai":
        pass # 特殊处理，见下文 join 逻辑
    elif status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Valid values: {sorted(_VALID_STATUSES | {'jingcai'})}")
    
    if match_type and match_type not in _VALID_MATCH_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid match_type. Valid values: {sorted(_VALID_MATCH_TYPES)}")

    q = db.query(Match).options(
        joinedload(Match.home_team),
        joinedload(Match.away_team),
        joinedload(Match.predictions)
    )

    if status == "jingcai":
        from models import JingcaiIssueMatch
        q = q.join(JingcaiIssueMatch, Match.id == JingcaiIssueMatch.match_id)
    elif status:
        q = q.filter(Match.status == status)

    if group:
        q = q.filter(Match.group == group.upper())
    if match_type:
        q = q.filter(Match.match_type == match_type.lower())
    if date == "today":
        from datetime import timezone as _tz, timedelta as _td
        now = datetime.now(_tz(_td(hours=8)))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + _td(days=1)
        q = q.filter(Match.kickoff_at >= start, Match.kickoff_at < end)
    if date == "tomorrow":
        from datetime import timezone as _tz, timedelta as _td
        now = datetime.now(_tz(_td(hours=8)))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + _td(days=1)
        end = start + _td(days=1)
        q = q.filter(Match.kickoff_at >= start, Match.kickoff_at < end)
    total = q.count()
    items = q.order_by(Match.kickoff_at.desc()).offset(offset).limit(min(limit, 200)).all()
    return {"total": total, "offset": offset, "limit": limit, "items": items}

@router.get("/{match_id}", response_model=MatchOut)
def get_match(match_id: int, db: Session = Depends(get_db)):
    """Get match details - free."""
    match = db.query(Match).options(
        joinedload(Match.home_team),
        joinedload(Match.away_team),
        joinedload(Match.predictions)
    ).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match

@router.get("/{match_id}/strategy", response_model=StrategyResponse)
def get_strategy(
    match_id: int,
    risk_tier: str = "balanced",
    db: Session = Depends(get_db),
    user: Optional[any] = Depends(get_optional_user),
):
    """
    Get prediction strategy for a match.
    Requires paid license unless the match has finished (auto-unlock).
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    is_finished = match.status == MatchStatus.FINISHED
    TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
    
    if not is_finished and not TEST_MODE:
        if user is None or not user.is_paid:
            raise HTTPException(
                status_code=403,
                detail="Strategy requires paid access. Redeem a license key or wait until the match finishes.",
            )
        if user.paid_until:
            from datetime import timezone as _tz
            paid_until = user.paid_until.replace(tzinfo=_tz.utc) if user.paid_until.tzinfo is None else user.paid_until
            if paid_until < datetime.now(_tz.utc):
                user.is_paid = False
                db.commit()
                raise HTTPException(
                    status_code=403,
                    detail="License expired. Please redeem a new license key.",
                )

    preds = db.query(Prediction).filter(Prediction.match_id == match_id).all()
    if not preds:
        raise HTTPException(status_code=404, detail="Predictions not found for this match")

    predictions = [
        {
            "id": p.id,
            "match_id": p.match_id,
            "play_type": p.play_type,
            "probabilities": p.probabilities,
            "model_version": p.model_version,
            "input_checksum": p.input_checksum,
            "locked_at": p.locked_at.isoformat() if p.locked_at else None,
        }
        for p in preds
    ]

    pipeline = StrategyPipeline(risk_tier=risk_tier, bankroll=100.0)
    picks = pipeline.generate(
        predictions=predictions,
        odds_home=match.odds_home or 2.0,
        odds_draw=match.odds_draw or 3.2,
        odds_away=match.odds_away or 3.5,
        competition=match.competition or "",
        match_id=match_id,
    )

    strategies = [
        StrategyPickOut(
            strategy_name=p.strategy_name,
            strategy_type=p.risk_tier,
            play_type=p.play_type,
            play_label=p.play_label,
            selection=p.selection,
            selection_label=p.selection_label,
            probability=p.model_prob_calibrated,
            odds=p.odds,
            ev=p.ev,
            kelly_fraction=p.kelly_raw,
            stake_pct=p.stake_pct,
            confidence=p.confidence,
            rationale=_enrich_rationale(p, match),
            risk_level=p.risk_label,
            risk_tier=p.risk_tier,
            model_prob_calibrated=p.model_prob_calibrated,
            market_prob=p.market_prob,
            edge=p.edge,
            var_95=p.var_95,
            cvar_95=p.cvar_95,
            risk_score=p.risk_score,
            is_recommended=p.is_recommended,
        )
        for p in picks
    ]

    return {
        "match_id": match_id,
        "status": match.status.value if hasattr(match.status, 'value') else match.status,
        "confidence": match.confidence or "medium",
        "odds_degraded": match.odds_degraded or False,
        "risk_tier": risk_tier,
        "strategies": [s.model_dump() for s in strategies],
        "predictions": predictions,
    }

@router.get("/{match_id}/odds-movement", response_model=OddsMovementResponse)
def get_odds_movement(
    match_id: int,
    db: Session = Depends(get_db),
):
    """Odds movement analysis for a match: opening→closing drift, steam moves, late money."""
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    tracker = OddsTracker(db)
    report = tracker.analyze_match(match_id)

    return {
        "match_id": match_id,
        "has_opening": report.has_opening,
        "opening_odds": {
            "home": report.opening_odds.odds_home,
            "draw": report.opening_odds.odds_draw,
            "away": report.opening_odds.odds_away,
            "source": report.opening_odds.source,
            "at": report.opening_odds.recorded_at.isoformat() if report.opening_odds else None,
        } if report.opening_odds else None,
        "closing_odds": {
            "home": report.closing_odds.odds_home,
            "draw": report.closing_odds.odds_draw,
            "away": report.closing_odds.odds_away,
        } if report.closing_odds else None,
        "drift": {
            "home_pct": report.drift_home_pct,
            "draw_pct": report.drift_draw_pct,
            "away_pct": report.drift_away_pct,
        },
        "steam_moves": [
            {
                "selection": s.selection,
                "from_odds": s.from_odds,
                "to_odds": s.to_odds,
                "change_pct": round(s.change_pct, 4),
                "window_minutes": round(s.window_minutes, 1),
                "direction": s.direction,
            }
            for s in report.steam_moves
        ],
        "late_money": [
            {
                "selection": s.selection,
                "from_odds": s.from_odds,
                "to_odds": s.to_odds,
                "change_pct": round(s.change_pct, 4),
                "direction": s.direction,
            }
            for s in report.late_money
        ],
        "signal": report.signal,
        "snapshots": {
            "total": report.total_snapshots,
            "real": report.real_snapshots,
        },
    }
