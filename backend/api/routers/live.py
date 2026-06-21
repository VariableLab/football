from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional
import json
import asyncio
import os

from database.config import get_settings
from database.models import get_db
from schemas import (
    LiveOddsAllResponse, LiveOddsSingleResponse,
    HedgeAlertsResponse, HedgePositionResponse, HedgeComputeResult,
    StatusResponse
)
from utils.logger import get_logger
from live_odds_feed import LiveOddsFeed, OddsBus, get_odds_bus, live_odds_update_to_dict
from live_hedge_engine import LiveHedgeEngine

settings = get_settings()
logger = get_logger("live_router")

router = APIRouter(prefix="/api", tags=["Live"])

# NOTE: 模块级全局变量，仅适用于单worker部署。
# 多worker场景需迁移至Redis共享状态。
_live_feed: Optional[LiveOddsFeed] = None
_live_hedge: Optional[LiveHedgeEngine] = None
_live_feed_lock = asyncio.Lock()

from api.auth import verify_admin_key

@router.get("/live-odds/stream")
async def live_odds_sse(request: Request):
    """SSE endpoint: 实时赔率推送。"""
    from sse_starlette.sse import EventSourceResponse

    bus = get_odds_bus()

    async def event_generator():
        queue: list = []
        sub = bus.subscribe(
            callback=lambda update: queue.append(update),
            min_change_pct=0.005,
        )

        try:
            while True:
                if await request.is_disconnected():
                    break
                while queue:
                    update = queue.pop(0)
                    yield {
                        "event": "odds_update",
                        "data": json.dumps(live_odds_update_to_dict(update)),
                    }
                await asyncio.sleep(0.5)
        finally:
            bus.unsubscribe(sub)

    return EventSourceResponse(event_generator())


@router.get("/live-odds/{match_id}", response_model=LiveOddsSingleResponse)
def get_live_odds(
    match_id: int,
    bus: OddsBus = Depends(get_odds_bus),
):
    """获取某场比赛的最新滚球赔率和历史。"""
    latest = bus.get_latest(match_id)
    history = bus.get_history(match_id, limit=20)

    return {
        "match_id": match_id,
        "latest": live_odds_update_to_dict(latest) if latest else None,
        "history": [live_odds_update_to_dict(u) for u in history],
    }


@router.get("/live-odds", response_model=LiveOddsAllResponse)
def get_all_live_odds():
    """获取所有比赛的最新滚球赔率。"""
    bus = get_odds_bus()
    all_latest = bus.get_all_latest()

    return {
        "count": len(all_latest),
        "updates": {
            str(mid): live_odds_update_to_dict(u)
            for mid, u in all_latest.items()
        },
    }


@router.post("/live-odds/start", response_model=StatusResponse)
def start_live_feed(_: bool = Depends(verify_admin_key)):
    """启动滚球赔率采集。"""
    global _live_feed, _live_hedge

    if _live_feed and _live_feed.is_running:
        return {"status": "already_running"}
    
    if os.getenv("WC_ENV", "").lower() in ("production", "prod") and os.getenv("WORKERS", "1") != "1":
        logger.warning("LiveOdds global state is not safe with multiple workers. Set WORKERS=1 or migrate to Redis.")

    _live_feed = LiveOddsFeed(
        bus=get_odds_bus(),
        poll_interval=settings.LIVE_ODDS_POLL_INTERVAL,
        fast_interval=settings.LIVE_ODDS_FAST_INTERVAL,
        pre_kickoff_minutes=settings.LIVE_ODDS_PRE_KICKIN_MINUTES,
        use_simulated=True,
    )

    if settings.ODDS_API_KEY:
        _live_feed.init_api_source(settings.ODDS_API_KEY)

    _live_feed.start()

    _live_hedge = LiveHedgeEngine(bus=get_odds_bus())
    _live_hedge.start_monitoring()

    return {"status": "started"}


@router.post("/live-odds/stop", response_model=StatusResponse)
def stop_live_feed(_: bool = Depends(verify_admin_key)):
    """停止滚球赔率采集。"""
    global _live_feed, _live_hedge

    if _live_feed:
        _live_feed.stop()
    if _live_hedge:
        _live_hedge.stop_monitoring()

    return {"status": "stopped"}


@router.get("/live-hedge/alerts", response_model=HedgeAlertsResponse)
def get_live_hedge_alerts():
    """获取滚球对冲警报列表。"""
    global _live_hedge
    if _live_hedge is None:
        return {"alerts": [], "positions": {}}

    alerts = _live_hedge.recent_alerts
    return {
        "alerts": [
            {
                "match_id": a.match_id,
                "level": a.alert_level.value,
                "type": a.hedge_type.value,
                "message": a.message,
                "current_odds": a.current_odds,
                "profit_pct": round(a.profit_pct, 4),
                "timestamp": a.timestamp.isoformat(),
            }
            for a in alerts
        ],
        "positions": {
            str(mid): {
                "selection": p.selection,
                "odds": p.odds,
                "stake": p.stake,
            }
            for mid, p in _live_hedge.positions.items()
        },
    }


@router.post("/live-hedge/position", response_model=HedgePositionResponse)
def add_hedge_position(
    match_id: int,
    selection: str,
    odds: float,
    stake: float,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """添加已有仓位用于滚球对冲计算。"""
    global _live_hedge
    if _live_hedge is None:
        _live_hedge = LiveHedgeEngine(bus=get_odds_bus())

    from live_hedge_engine import Position
    _live_hedge.add_position(Position(
        match_id=match_id,
        selection=selection,
        odds=odds,
        stake=stake
    ))
    return {"status": "added"}


@router.post("/live-hedge/compute", response_model=HedgeComputeResult)
def compute_hedge(
    match_id: int,
    selection: str,
    odds: float,
    stake: float,
):
    """计算对冲方案。"""
    engine = LiveHedgeEngine(bus=get_odds_bus())
    from live_hedge_engine import Position
    p = Position(match_id=match_id, selection=selection, odds=odds, stake=stake)
    
    bus = get_odds_bus()
    latest = bus.get_latest(match_id)
    if not latest:
        raise HTTPException(404, "No live odds for this match")
        
    result = engine.compute_hedge_delta(p, latest.odds)
    return result
