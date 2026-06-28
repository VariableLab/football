from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from datetime import datetime
import os

from database.models import get_db, Match, MatchStatus, Prediction
from schemas import (
    MatchListResponse, MatchOut, StrategyResponse, 
    OddsMovementResponse, StrategyPickOut, PortfolioStrategyOut
)
from auth import get_optional_user
from strategy_pipeline import StrategyPipeline
from utils.cache import cached_api


router = APIRouter(prefix="/api/matches", tags=["matches"])

_VALID_STATUSES = {"upcoming", "live", "finished", "postponed", "cancelled", "future"}
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
        joinedload(Match.away_team)
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
        q = q.filter(Match.group_name == group.upper())
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
    model_tier: str = "aligned",
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
    TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
    
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

    version_map = {
        "classic": "v3.0_classic",
        "aligned": "v3.0",
        "deep": "v4.0"
    }
    target_version = version_map.get(model_tier, "v3.0")
    fallback_sequence = [target_version, "v3.0", "v2.0", "v3.0_shadow"]
    fallback_sequence = list(dict.fromkeys(fallback_sequence))

    preds = []
    for ver in fallback_sequence:
        preds = db.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.model_version == ver
        ).all()
        if preds:
            break
    
    # 如果数据库中没有预测，则即时生成（实时计算模式）
    if not preds:
        try:
            from core.prediction_engine import PredictionEngine, build_context_from_match
            ctx = build_context_from_match(match)
            engine = PredictionEngine(db_session=db)
            result = engine.predict(ctx)
            
            # 模拟数据库负载结构
            predictions = []
            payloads = result.to_db_payload()
            
            selected_payloads = []
            for ver in fallback_sequence:
                selected_payloads = [p for p in payloads if p.get("model_version") == ver]
                if selected_payloads:
                    break
            
            for payload in selected_payloads:
                predictions.append({
                    "id": 0, # 虚拟 ID
                    "match_id": match.id,
                    "play_type": payload["play_type"],
                    "probabilities": payload["probabilities"],
                    "model_version": payload["model_version"],
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
            kelly_fraction=p.kelly_raw,
            stake_pct=p.stake_pct,
            rationale=_enrich_rationale(p, match),
            risk_level=p.risk_label,
            confidence=p.confidence,
            is_recommended=p.is_recommended,
        )
        for p in picks
    ]

    # ─── 混合比分模型: 计算崩盘概率 ───
    collapse_prob = 0.0
    big_score_warning = False
    upset_signal = None

    # 先从预测结果中尝试提取 mixture_signals (如果已持久化)
    for pred in predictions:
        probs = pred.get("probabilities", {})
        if isinstance(probs, dict) and "_mixture" in probs:
            mix = probs["_mixture"]
            if isinstance(mix, dict):
                collapse_prob = mix.get("collapse_prob", 0.0)
                big_score_warning = mix.get("big_score_warning", False)
                upset_signal = mix.get("upset_signal")
            break

    # 如果没有持久化的信号，则实时计算
    if collapse_prob == 0.0 and upset_signal is None:
        try:
            from core.models.mixture_score_model import MixtureScoreModel
            from core.models.upset_detector import UpsetDetector

            home_elo = match.home_team.elo if match.home_team else 0
            away_elo = match.away_team.elo if match.away_team else 0
            elo_diff = home_elo - away_elo

            home_xg = (match.home_team.avg_xg or match.home_team.avg_goals_scored) if match.home_team else 0
            away_xg = (match.away_team.avg_xg or match.away_team.avg_goals_conceded) if match.away_team else 0
            xg_diff = home_xg - away_xg

            home_form = (match.home_team.form_factor or 1.0) if match.home_team else 1.0
            away_form = (match.away_team.form_factor or 1.0) if match.away_team else 1.0

            home_inj = len([x for x in ((match.home_team.key_injuries or "") if match.home_team else "") .split(",") if x.strip()])
            away_inj = len([x for x in ((match.away_team.key_injuries or "") if match.away_team else "") .split(",") if x.strip()])

            home_rest = getattr(match.home_team, "rest_days", 7) if match.home_team else 7
            away_rest = getattr(match.away_team, "rest_days", 7) if match.away_team else 7

            collapse_prob = MixtureScoreModel.compute_collapse_probability(
                elo_diff=elo_diff, xg_diff=xg_diff,
                home_form=home_form, away_form=away_form,
                home_injuries=home_inj, away_injuries=away_inj,
                home_rest_days=home_rest, away_rest_days=away_rest,
            )
            big_score_warning = collapse_prob > 0.25

            # 爆冷探测器
            spf_pred = next((p for p in predictions if p.get("play_type") == "SPF"), None)
            if spf_pred:
                model_spf = spf_pred.get("probabilities", {})
                if isinstance(model_spf, dict) and len(model_spf) >= 3:
                    signal = UpsetDetector.detect_from_odds(
                        model_spf=model_spf,
                        odds_home=match.odds_home or 2.0,
                        odds_draw=match.odds_draw or 3.2,
                        odds_away=match.odds_away or 3.5,
                    )
                    upset_signal = {
                        "kl_divergence": signal.kl_divergence,
                        "upset_probability": signal.upset_probability,
                        "divergence_direction": signal.divergence_direction,
                        "is_upset_candidate": signal.is_upset_candidate,
                        "confidence": signal.confidence,
                    }
        except Exception as e:
            import logging
            logging.getLogger("matches").debug(f"Model signals failed: {e}")

    # ─── 对冲投资组合 ───
    try:
        from strategy.hedged_portfolio import HedgedPortfolioGenerator
        from strategy.ev_maximizing_strategy import EVMaximizingStrategy
        
        port_gen = HedgedPortfolioGenerator(
            match_predictions=predictions,
            odds_home=match.odds_home or 2.0,
            odds_draw=match.odds_draw or 3.2,
            odds_away=match.odds_away or 3.5,
            collapse_prob=collapse_prob,
        )
        raw_ports = port_gen.generate(min_ev=0.03)
        
        ev_gen = EVMaximizingStrategy(
            match_predictions=predictions,
            odds_home=match.odds_home or 2.0,
            odds_draw=match.odds_draw or 3.2,
            odds_away=match.odds_away or 3.5,
            collapse_prob=collapse_prob,
        )
        ev_ports = ev_gen.generate(min_ev=0.03)
        
        merged_ports = raw_ports + ev_ports
        merged_ports.sort(key=lambda p: p.expected_roi, reverse=True)
        
        # 提取 dict 格式的 portfolio
        portfolios = []
        for port in merged_ports[:6]:
            portfolios.append({
                "strategy_type": port.strategy_type,
                "name": port.name,
                "legs": [
                    {
                        "type": leg.leg_type,
                        "play": leg.play,
                        "selection": leg.selection,
                        "odds": leg.odds,
                        "probability": leg.probability,
                        "stake_pct": leg.stake_pct
                    }
                    for leg in port.legs
                ],
                "expected_roi": port.expected_roi,
                "win_prob_combined": port.win_prob_combined,
                "rationale": port.rationale,
                "total_ev": port.total_ev,
                "kelly_fraction": port.kelly_fraction,
            })
    except Exception as e:
        import logging
        logging.getLogger("matches").error(f"Portfolio generation failed: {e}")
        portfolios = []

    return StrategyResponse(
        match_id=match_id,
        status=match.status,
        confidence=match.confidence or "medium",
        odds_degraded=match.odds_degraded,
        risk_tier=risk_tier,
        strategies=strategies,
        portfolios=portfolios,
        predictions=predictions,
        collapse_prob=round(collapse_prob, 4),
        big_score_warning=big_score_warning,
        upset_signal=upset_signal,
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
