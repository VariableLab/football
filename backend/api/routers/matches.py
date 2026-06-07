from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from datetime import datetime, timezone, timedelta
import os

from database.models import get_db, Match, MatchStatus, Prediction
from schemas import (
    MatchListResponse, MatchOut, StrategyResponse, 
    OddsMovementResponse, StrategyPickOut
)
from auth import get_optional_user
from strategy_pipeline import StrategyPipeline
from odds_tracker import OddsTracker
from utils.cache import cached_api


router = APIRouter(prefix="/api/matches", tags=["matches"])

_VALID_STATUSES = {"upcoming", "live", "finished", "postponed", "cancelled"}
_VALID_MATCH_TYPES = {"world_cup", "friendly", "warm_up", "qualifier"}

def _enrich_rationale(pick, match) -> str:
    """将 pipeline 的技术性 rationale 增强为模型研究分析。"""
    sel_label = pick.selection_label
    parts: list[str] = []

    # 1. 核心模型估算原因
    if pick.edge > 0.05:
        parts.append(f"{sel_label}模型偏差显著: 模型概率{pick.model_prob_calibrated:.0%}显著偏离市场共识{pick.market_prob:.0%}，统计偏差{pick.edge:+.1%}")
    elif pick.edge > 0:
        parts.append(f"{sel_label}存在正向偏差: 模型估值{pick.model_prob_calibrated:.1%}略高于市场快照折算")

    # 2. 球队基本面
    home = match.home_team
    away = match.away_team
    if home and away:
        if home.elo and away.elo:
            elo_diff = home.elo - away.elo
            if elo_diff > 150:
                parts.append(f"{home.name}历史实力基准占优(ELO +{elo_diff})")
            elif elo_diff < -150:
                parts.append(f"{away.name}历史实力基准占优(ELO {elo_diff})")

    # 3. 状态因子
    if home and home.form_factor and home.form_factor > 1.1:
        parts.append(f"{home.name}近期统计状态良好(系数{home.form_factor:.2f})")
    if away and away.form_factor and away.form_factor > 1.1:
        parts.append(f"{away.name}近期统计状态良好(系数{away.form_factor:.2f})")

    # 4. 置信度
    if pick.confidence == "high":
        parts.append("高信心统计校准")
    elif pick.confidence == "low":
        parts.append("低信心样本，仅供参考")

    # 5. 偏差程度
    if pick.risk_label in ("high", "extreme"):
        parts.append(f"偏差程度{pick.risk_label}，建议关注模型稳定性")

    return "。".join(parts)

@router.get("", response_model=MatchListResponse)
@cached_api(ttl_seconds=300) # 增加到 5 分钟加速响应
def list_matches(
    status: str = None,
    group: str = None,
    match_type: str = None,
    date: str = None,
    limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    列出研究样本赛事。
    支持通过状态（含 'jingcai' 批次）、分组、类型及日期进行过滤。
    """
    if status == "jingcai":
        pass # 特殊处理，见下文 join 逻辑
    elif status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Valid values: {sorted(_VALID_STATUSES | {'jingcai'})}")
    
    if match_type and match_type not in _VALID_MATCH_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid match_type. Valid values: {sorted(_VALID_MATCH_TYPES)}")

    # 使用 selectinload 优化集合加载性能
    q = db.query(Match).options(
        joinedload(Match.home_team),
        joinedload(Match.away_team),
        selectinload(Match.predictions)
    )

    if status == "jingcai":
        from database.models import JingcaiIssueMatch
        q = q.join(JingcaiIssueMatch, Match.id == JingcaiIssueMatch.match_id)
    elif status == "future":
        # 💡 特殊状态：所有未完赛场次
        q = q.filter(Match.status != "finished")
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
        selectinload(Match.predictions)
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
    获取单场赛事的模型推演策略。
    未完赛场次需要有效的授权码访问（赛后自动公开验证）。
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
    
    # 如果数据库中没有预测，则即时生成（实时计算模式）
    if not preds:
        try:
            from prediction_engine import PredictionEngine, build_context_from_match
            ctx = build_context_from_match(match)
            engine = PredictionEngine(db_session=db)
            result = engine.predict(ctx)
            
            # 模拟数据库负载结构
            predictions = []
            for payload in result.to_db_payload():
                predictions.append({
                    "id": 0, # 虚拟 ID
                    "match_id": match.id,
                    "play_type": payload["play_type"],
                    "probabilities": payload["probabilities"],
                    "model_version": payload.get("model_version", "v1.0"),
                    "input_checksum": "live-calc",
                    "locked_at": None,
                })
        except Exception as e:
            import logging
            logging.getLogger("matches").error(f"Live prediction failed for match {match_id}: {e}")
            raise HTTPException(status_code=404, detail="Predictions not found and live calculation failed")
    else:
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
            edge=p.edge,
            kelly_stake=p.kelly_stake,
            rationale=_enrich_rationale(p, match),
            risk_label=p.risk_label,
            confidence=p.confidence,
        )
        for p in picks
    ]

    return StrategyResponse(
        match_id=match_id,
        status=match.status,
        confidence=match.confidence or "medium",
        odds_degraded=match.odds_degraded,
        risk_tier=risk_tier,
        strategies=strategies,
        predictions=predictions,
    )

@router.get("/{match_id}/odds-movement", response_model=OddsMovementResponse)
def get_odds_movement(match_id: int, db: Session = Depends(get_db)):
    """Get historical odds movement for a match."""
    from database.models import OddsHistory
    
    # 1. 基础开/收盘赔率
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # 2. 历史趋势
    history = db.query(OddsHistory).filter(
        OddsHistory.match_id == match_id
    ).order_by(OddsHistory.recorded_at.asc()).all()

    return {
        "match_id": match_id,
        "opening": {
            "h": match.opening_odds_home,
            "d": match.opening_odds_draw,
            "a": match.opening_odds_away,
            "at": match.opening_odds_at.isoformat() if match.opening_odds_at else None
        },
        "closing": {
            "h": match.closing_odds_home,
            "d": match.closing_odds_draw,
            "a": match.closing_odds_away,
            "at": match.odds_locked_at.isoformat() if match.odds_locked_at else None
        },
        "history": [
            {
                "h": h.odds_home,
                "d": h.odds_draw,
                "a": h.odds_away,
                "at": h.recorded_at.isoformat(),
                "source": h.source
            } for h in history
        ]
    }
