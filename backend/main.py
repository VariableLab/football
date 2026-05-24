# -*- coding: utf-8 -*-
import hmac
import json
import os
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from contextlib import asynccontextmanager

from logger import get_logger
from config import get_settings
from models import init_db, get_db, User, Team, Match, MatchStatus, Prediction, JingcaiIssue, JingcaiIssueMatch, OddsHistory
from schemas import (
    UserRegister, UserLogin, UserOut, Token,
    LicenseRedeem, LicenseRedeemOut,
    MatchOut, PredictionOut, StrategyPickOut,
    JingcaiIssueCreate, JingcaiIssueOut, JingcaiIssueResultIn,
    JingcaiIssueListResponse,
    MatchListResponse, StrategyResponse,
    FeedbackOut, FeedbackListResponse,
    JingcaiReportResponse,
    # Phase 2 response_model additions
    TeamListResponse,
    SettingsResponse, SettingsUpdateResponse,
    HealthCheck, OddsMovementResponse, ArbitrageResponse,
    ValidationReportResponse, CalibrationCurveResponse, PlayTypeBreakdownResponse,
    BetNNStatusResponse, BetNNPredictResponse, BetNNTrainResponse,
    FeedbackLikeResponse, FeedbackCreateResponse,
    LiveOddsAllResponse, LiveOddsSingleResponse,
    HedgeAlertsResponse, HedgePositionResponse, HedgeComputeResult,
    OptimalComboResponse, StatusResponse,
)
from strategy_pipeline import StrategyPipeline
from odds_tracker import OddsTracker
from hedge_engine import HedgeEngine
from live_odds_feed import LiveOddsFeed, OddsBus, get_odds_bus, live_odds_update_to_dict
from live_hedge_engine import LiveHedgeEngine
from auth import get_password_hash, verify_password, create_access_token, get_current_active_user, get_optional_user
from license_manager import redeem_license_key
from admin import router as admin_router
from routers.matches import router as matches_router
from routers.feedback import router as feedback_router
from routers.monitor import router as monitor_router
from routers.advisor import router as advisor_router
from validation_engine import ValidationEngine
from odds_collector import OddsCollector, collect_odds_tier1_primary

settings = get_settings()
logger = get_logger("main")


# ────────────────────────────
# Security Headers Middleware
# ────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up", extra={"extra_data": {"version": "0.1.0"}})

    # 生产环境安全守卫
    if settings.DEBUG and os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError("DEBUG=True is not allowed in production")

    init_db()
    from scheduler import start_scheduler
    start_scheduler()
    yield
    from scheduler import stop_scheduler
    stop_scheduler()
    logger.info("Application shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if not settings.DEBUG else "/docs",
    redoc_url=None if not settings.DEBUG else "/redoc",
    openapi_url=None if not settings.DEBUG else "/openapi.json",
)

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Global exception handler — prevent info leakage in production
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def safe_http_exception(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

@app.exception_handler(Exception)
async def safe_generic_exception(request, exc):
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}")
    detail = str(exc) if settings.DEBUG else "Internal server error"
    return JSONResponse(status_code=500, content={"detail": detail})

# CORS — 生产环境必须配置ALLOWED_ORIGINS
if settings.DEBUG:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Api-Key"],
    )
else:
    _production_origins = os.getenv("ALLOWED_ORIGINS", "").split(",")
    _production_origins = [o.strip() for o in _production_origins if o.strip()]
    if not _production_origins:
        _is_production = os.getenv("WC_ENV", "").lower() in ("production", "prod")
        if _is_production:
            raise ValueError(
                "ALLOWED_ORIGINS environment variable is required in production. "
                "Set it to a comma-separated list of allowed origins, e.g.: "
                "ALLOWED_ORIGINS=https://example.com,https://app.example.com"
            )
        # Non-production without ALLOWED_ORIGINS: allow localhost for dev
        logger.warning("ALLOWED_ORIGINS not set. Using localhost fallback. Set ALLOWED_ORIGINS for production.")
        _production_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_production_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Api-Key"],
    )
    # HTTPS 强制默认关闭，由 Nginx 反代层处理 HTTP→HTTPS 重定向
    # 如需在应用层强制 HTTPS，设置 ENFORCE_HTTPS=1
    if os.getenv("ENFORCE_HTTPS", "0") == "1":
        app.add_middleware(HTTPSRedirectMiddleware)

# Admin routes (for OpenClaw)
app.include_router(admin_router)
app.include_router(matches_router)
app.include_router(feedback_router)
app.include_router(monitor_router)
app.include_router(advisor_router)

# Static files — 使用绝对路径
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    """首页 — 返回前端入口"""
    return FileResponse(str(STATIC_DIR / "index.html"))


# ────────────────────────────
# Auth
# ────────────────────────────
@app.post("/api/auth/register", response_model=Token)
@limiter.limit("5/hour")
def register(request: Request, data: UserRegister, db: Session = Depends(get_db)):
    """Register a new user."""
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        # Return same response format to prevent user enumeration
        # Use a random invalid token that will fail on /api/auth/me
        import secrets as _secrets
        fake_token = f"enum.{_secrets.token_urlsafe(32)}"
        return {"access_token": fake_token}

    user = User(
        email=data.email,
        password_hash=get_password_hash(data.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@app.post("/api/auth/login", response_model=Token)
@limiter.limit("10/hour")
def login(request: Request, data: UserLogin, db: Session = Depends(get_db)):
    """Login and get access token."""
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@app.get("/api/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_active_user)):
    """Get current user info."""
    return current_user


# ────────────────────────────
# License / Payment
# ────────────────────────────
@app.post("/api/license/redeem", response_model=LicenseRedeemOut)
@limiter.limit("10/hour")
def redeem(request: Request,
    data: LicenseRedeem,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Redeem a license key to unlock access."""
    result = redeem_license_key(db, current_user, data.key.strip().upper())
    if not result:
        raise HTTPException(status_code=400, detail="Invalid or used license key")

    return LicenseRedeemOut(
        success=True,
        license_type=result.license.license_type.value,
        message="License activated successfully"
    )


# ────────────────────────────
# Public Data (Free)
# ────────────────────────────
@app.get("/api/teams", response_model=TeamListResponse)
def list_teams(
    limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db),
):
    """List teams with pagination."""
    total = db.query(Team).count()
    items = db.query(Team).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ────────────────────────────
# Arbitrage Scanner (Public)
# ────────────────────────────
@app.get("/api/arbitrage", response_model=ArbitrageResponse)
def get_arbitrage_opportunities(
    competition: str = "",
    db: Session = Depends(get_db),
):
    """Scan for cross-bookmaker arbitrage opportunities from MatchBookmakerOdds."""
    engine = HedgeEngine(db)
    opportunities = engine.scan_arbitrage(competition=competition)

    return {
        "count": len(opportunities),
        "opportunities": [
            {
                "match_id": o.match_id,
                "best_odds": {
                    "home": o.best_home_odds,
                    "draw": o.best_draw_odds,
                    "away": o.best_away_odds,
                },
                "bookmakers": {
                    "home": o.home_bookmaker,
                    "draw": o.draw_bookmaker,
                    "away": o.away_bookmaker,
                },
                "implied_total": round(o.implied_total, 4),
                "profit_pct": round(o.profit_pct, 4),
                "net_profit_pct": round(o.net_profit_pct, 4),
                "stakes": o.stakes,
                "is_genuine": o.is_genuine,
            }
            for o in opportunities
        ],
    }


# ────────────────────────────
# Live Odds (SSE + REST)
# ────────────────────────────
# NOTE: 模块级全局变量，仅适用于单worker部署。
# 多worker场景需迁移至Redis共享状态（见架构升级路径）。
import warnings as _warnings
_live_feed: Optional[LiveOddsFeed] = None
_live_hedge: Optional[LiveHedgeEngine] = None
_live_feed_lock = asyncio.Lock()  # 防止并发启动/停止


@app.get("/api/live-odds/stream")
async def live_odds_sse(request: Request):
    """SSE endpoint: 实时赔率推送。前端用 EventSource('/api/live-odds/stream') 订阅。"""
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


@app.get("/api/live-odds/{match_id}", response_model=LiveOddsSingleResponse)
def get_live_odds(
    match_id: int,
    bus: OddsBus = Depends(get_odds_bus),
):
    """获取某场比赛的最新滚球赔率和历史。"""
    bus = get_odds_bus()
    latest = bus.get_latest(match_id)
    history = bus.get_history(match_id, limit=20)

    return {
        "match_id": match_id,
        "latest": live_odds_update_to_dict(latest) if latest else None,
        "history": [live_odds_update_to_dict(u) for u in history],
    }


@app.get("/api/live-odds", response_model=LiveOddsAllResponse)
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


def _verify_admin_key(x_api_key: str = Header(..., alias="X-Api-Key")) -> bool:
    """验证 Admin API Key — Header 传递，常量时间比较"""
    if not hmac.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return True


@app.post("/api/live-odds/start", response_model=StatusResponse)
def start_live_feed(_: bool = Depends(_verify_admin_key)):
    """启动滚球赔率采集。"""
    global _live_feed, _live_hedge

    if _live_feed and _live_feed.is_running:
        return {"status": "already_running"}
    if os.getenv("WC_ENV", "").lower() in ("production", "prod") and os.getenv("WORKERS", "1") != "1":
        logger.warning("LiveOdds global state is not safe with multiple workers. Set WORKERS=1 or migrate to Redis.")

    from config import get_settings
    settings = get_settings()

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


@app.post("/api/live-odds/stop", response_model=StatusResponse)
def stop_live_feed(_: bool = Depends(_verify_admin_key)):
    """停止滚球赔率采集。"""
    global _live_feed, _live_hedge

    if _live_feed:
        _live_feed.stop()
    if _live_hedge:
        _live_hedge.stop_monitoring()

    return {"status": "stopped"}


# ────────────────────────────
# Live Hedge Alerts
# ────────────────────────────
@app.get("/api/live-hedge/alerts", response_model=HedgeAlertsResponse)
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


@app.post("/api/live-hedge/position", response_model=HedgePositionResponse)
def add_hedge_position(
    match_id: int,
    selection: str,
    odds: float,
    stake: float,
    db: Session = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
):
    """添加已有仓位用于滚球对冲计算。"""
    global _live_hedge
    if _live_hedge is None:
        _live_hedge = LiveHedgeEngine(bus=get_odds_bus())

    from live_hedge_engine import Position
    from datetime import datetime, timezone

    _live_hedge.add_position(Position(
        match_id=match_id,
        selection=selection,
        odds=odds,
        stake=stake,
        placed_at=datetime.now(timezone.utc),
    ))

    return {"status": "added", "match_id": match_id}


@app.get("/api/live-hedge/compute/{match_id}", response_model=HedgeComputeResult)
def compute_live_hedge(
    match_id: int,
    selection: str = "home",
    odds: float = 2.0,
    stake: float = 10.0,
    fraction: float = 1.0,
    db: Session = Depends(get_db),
):
    """计算滚球对冲方案。"""
    global _live_hedge
    if _live_hedge is None:
        _live_hedge = LiveHedgeEngine(bus=get_odds_bus())

    result = _live_hedge.compute_live_hedge(
        match_id=match_id,
        original_selection=selection,
        original_odds=odds,
        original_stake=stake,
        hedge_fraction=fraction,
    )

    if result is None:
        return {"match_id": match_id, "hedge_available": False}

    return {
        "match_id": match_id,
        "hedge_available": True,
        "hedge_stake": result.hedge_stake,
        "hedge_odds": result.hedge_odds,
        "guaranteed_profit": result.guaranteed_profit,
        "hedge_ratio": result.hedge_ratio,
        "profit_if_original_wins": result.profit_if_original_wins,
        "profit_if_hedge_wins": result.profit_if_hedge_wins,
        "is_profitable": result.is_profitable,
    }


# ────────────────────────────
# Validation (Public — 验证看板)
# ────────────────────────────
@app.get("/api/validation", response_model=ValidationReportResponse)
def public_validation(
    match_type: str = None,
    db: Session = Depends(get_db)
):
    """
    Public validation report — compares predictions vs actual results.
    No auth required. Used by the validation dashboard.
    """
    report = ValidationEngine.run_validation(db, match_type=match_type)
    return report.to_dict()


@app.get("/api/validation/calibration", response_model=CalibrationCurveResponse)
def calibration_curve(db: Session = Depends(get_db)):
    """Probability calibration curve (reliability diagram)."""
    return ValidationEngine.calibration_curve(db)


@app.get("/api/validation/by-play-type", response_model=PlayTypeBreakdownResponse)
def validation_by_play_type(db: Session = Depends(get_db)):
    """Accuracy breakdown by play type (SPF/RQ/Score/Goals/Half)."""
    return ValidationEngine.validate_by_play_type(db)


# ────────────────────────────
# Jingcai Issue（足彩期号）
# ────────────────────────────
def _convert_jingcai_odds(raw: dict, pool: str) -> dict | None:
    """将体彩原始赔率键名转为前端友好的格式"""
    if not raw:
        return None
    result = {}

    def _safe(val) -> float | None:
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    if pool == "rq":
        # hhad: h=主胜, d=平, a=客胜
        h = _safe(raw.get("h"))
        d = _safe(raw.get("d"))
        a = _safe(raw.get("a"))
        if h: result["home"] = h
        if d: result["draw"] = d
        if a: result["away"] = a
        if not result:
            return None

    elif pool == "score":
        # crs: s{home}s{away} = 比分赔率, e.g. s01s00 = 1:0
        import re
        for k, v in raw.items():
            m = re.match(r"s(\d+)s(\d+)$", k)
            if m:
                score_key = f"{int(m.group(1))}:{int(m.group(2))}"
                odd = _safe(v)
                if odd:
                    result[score_key] = odd
        # 其他比分
        other_map = {"s1sh": "主胜其他", "s1sd": "平其他", "s1sa": "客胜其他"}
        for k, label in other_map.items():
            odd = _safe(raw.get(k))
            if odd:
                result[label] = odd
        if not result:
            return None

    elif pool == "goals":
        # ttg: s0=0球, s1=1球, ..., s7=7+球
        for i in range(8):
            key = f"s{i}"
            odd = _safe(raw.get(key))
            label = f"{i}" if i < 7 else "7+"
            if odd:
                result[label] = odd
        if not result:
            return None

    elif pool == "half":
        # hafu: h=主(胜), d=平, a=客(负); hh=主主, hd=主平, etc.
        mapping = {
            "hh": "主主", "hd": "主平", "ha": "主客",
            "dh": "平主", "dd": "平平", "da": "平客",
            "ah": "客主", "ad": "客平", "aa": "客客",
        }
        for k, label in mapping.items():
            odd = _safe(raw.get(k))
            if odd:
                result[label] = odd
        if not result:
            return None

    else:
        return None

    return result


def _safe_parse_json(val):
    """Parse JSON column that may be stored as string or already-parsed dict."""
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    return val


def _issue_to_dict(issue: JingcaiIssue) -> dict:
    """将 JingcaiIssue ORM 对象转为 API dict"""
    matches = []
    for im in (issue.issue_matches or []):
        if im.match is None:
            continue
        match_data = MatchOut.model_validate(im.match).model_dump()
        matches.append({
            "sequence": im.sequence,
            "handicap": im.handicap,
            "rq_odds": _convert_jingcai_odds(json.loads(im.rq_odds), "rq") if im.rq_odds else None,
            "score_odds": _convert_jingcai_odds(json.loads(im.score_odds), "score") if im.score_odds else None,
            "goals_odds": _convert_jingcai_odds(json.loads(im.goals_odds), "goals") if im.goals_odds else None,
            "half_odds": _convert_jingcai_odds(json.loads(im.half_odds), "half") if im.half_odds else None,
            "match": match_data,
        })
    return {
        "id": issue.id,
        "issue_id": issue.issue_id,
        "issue_type": issue.issue_type,
        "status": issue.status,
        "sale_start": issue.sale_start.isoformat() if issue.sale_start else None,
        "sale_end": issue.sale_end.isoformat() if issue.sale_end else None,
        "draw_at": issue.draw_at.isoformat() if issue.draw_at else None,
        "draw_result": _safe_parse_json(issue.draw_result),
        "verification": _safe_parse_json(issue.verification),
        "notes": issue.notes,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "matches": matches,
    }


def _auto_close_expired_issues(db: Session) -> int:
    """关闭已过sale_end或所有比赛已完结的期号。由scheduler调用。"""
    from datetime import datetime, timedelta
    from models import Match, MatchStatus
    from sqlalchemy import func
    # SQLite 存储的是 naive datetime，使用 now 进行比较
    now = datetime.utcnow()
    expired = []

    # 条件1：明确超过销售截止时间的
    sale_end_expired = db.query(JingcaiIssue).filter(
        JingcaiIssue.status == "on_sale",
        JingcaiIssue.sale_end != None,
        JingcaiIssue.sale_end < now,
    ).all()
    expired.extend(sale_end_expired)

    # 条件2：所有比赛均已完结（status=finished 且 actual_outcome 非空）
    all_on_sale = db.query(JingcaiIssue).filter(
        JingcaiIssue.status == "on_sale",
    ).all()
    for issue in all_on_sale:
        if issue in expired:
            continue
        match_ids = [im.match_id for im in (issue.issue_matches or [])]
        if not match_ids:
            continue
        unfinished = db.query(Match).filter(
            Match.id.in_(match_ids),
            (Match.status != MatchStatus.FINISHED) | (Match.actual_outcome == None),
        ).count()
        if unfinished == 0:
            expired.append(issue)
            continue
        # 条件3：开赛超过48小时仍无赛果的比赛视为"结果缺失"
        stale = db.query(Match).filter(
            Match.id.in_(match_ids),
            Match.kickoff_at != None,
            Match.kickoff_at < now - timedelta(hours=48),
            Match.actual_outcome == None,
        ).all()
        if stale:
            for m in stale:
                m.status = MatchStatus.FINISHED
                m.actual_outcome = "abandoned"
                m.actual_home_goals = -1
                m.actual_away_goals = -1
                logger.warning(f"[auto-close] 标记比赛 {m.match_code or m.id} 为无结果(超48h)")
            db.commit()
            # 重新检查是否全部完赛
            unfinished = db.query(Match).filter(
                Match.id.in_(match_ids),
                (Match.status != MatchStatus.FINISHED) | (Match.actual_outcome == None),
            ).count()
            if unfinished == 0:
                expired.append(issue)

    for issue in expired:
        issue.status = "drawn"
        issue.draw_result = {"results": [], "auto_closed": True}
    if expired:
        db.commit()
        logger.info(f"[auto-close] 关闭了 {len(expired)} 个期号: {[e.issue_id for e in expired]}")
    return len(expired)


@app.post("/api/jingcai/issues/auto-close")
def auto_close_issues(_: bool = Depends(_verify_admin_key), db: Session = Depends(get_db)):
    """手动触发过期期号自动关闭。"""
    count = _auto_close_expired_issues(db)
    return {"closed": count}


@app.get("/api/jingcai/issues", response_model=JingcaiIssueListResponse)
def list_jingcai_issues(
    status: Optional[str] = None,
    limit: int = 20, offset: int = 0,
    db: Session = Depends(get_db),
):
    """列出足彩期号列表（只读）。"""

    query = db.query(JingcaiIssue).options(
        joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match)
    )
    if status:
        query = query.filter(JingcaiIssue.status == status)
    # 在售在前(按期号倒序)，已关闭在后
    total = query.count()
    issues = query.offset(offset).limit(min(limit, 100)).all()
    issues.sort(key=lambda i: (0 if i.status == "on_sale" else 1, i.issue_id or ""), reverse=False)
    issues.sort(key=lambda i: (0 if i.status == "on_sale" else 1))
    return {"total": total, "offset": offset, "limit": limit, "items": [_issue_to_dict(i) for i in issues]}


@app.get("/api/jingcai/issues/{issue_id}", response_model=JingcaiIssueOut)
def get_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
):
    """获取单期足彩详情（含比赛和预测）"""
    issue = db.query(JingcaiIssue).options(
        joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match).joinedload(Match.predictions)
    ).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_to_dict(issue)


@app.post("/api/jingcai/issues", response_model=JingcaiIssueOut)
def create_jingcai_issue(
    data: JingcaiIssueCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
):
    """创建足彩期号并关联比赛"""
    from jingcai_predictor import create_issue
    try:
        issue = create_issue(
            db,
            issue_id=data.issue_id,
            issue_type=data.issue_type,
            sale_start=data.sale_start,
            sale_end=data.sale_end,
            match_codes=data.match_codes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return issue


@app.post("/api/jingcai/issues/{issue_id}/predict")
def predict_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
):
    """为整期足彩生成预测"""
    from jingcai_predictor import predict_issue
    try:
        result = predict_issue(db, issue_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@app.post("/api/jingcai/issues/{issue_id}/results")
def record_jingcai_result(
    issue_id: str,
    data: JingcaiIssueResultIn,
    db: Session = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
):
    """录入开奖结果"""
    from jingcai_predictor import record_draw_result
    try:
        issue = record_draw_result(
            db, issue_id, data.results, data.prizes, data.draw_at
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return issue


@app.post("/api/jingcai/issues/{issue_id}/verify")
def verify_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
):
    """验证模型预测 vs 开奖结果"""
    from jingcai_predictor import verify_issue
    try:
        result = verify_issue(db, issue_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/api/jingcai/report", response_model=JingcaiReportResponse)
def jingcai_report(db: Session = Depends(get_db)):
    """每期预测报告：最高胜率模型估算 + 结果自分析"""
    issues = (
        db.query(JingcaiIssue)
        .options(joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match).joinedload(Match.predictions))
        .order_by(JingcaiIssue.issue_id.desc())
        .limit(50)
        .all()
    )

    reports = []
    for issue in issues:
        matches_report = []
        for im in (issue.issue_matches or []):
            match = im.match
            if not match:
                continue

            preds = match.predictions or []
            best_pick = None
            best_prob = 0.0

            for p in preds:
                ptype = p.play_type.value if hasattr(p.play_type, "value") else str(p.play_type)
                probs = p.probabilities or {}
                for sel, prob in probs.items():
                    if prob > best_prob:
                        best_prob = prob
                        best_pick = {
                            "play_type": ptype,
                            "selection": sel,
                            "probability": prob,
                        }

            home = match.home_team.name if match.home_team else "?"
            away = match.away_team.name if match.away_team else "?"

            mr = {
                "sequence": im.sequence,
                "home": home,
                "away": away,
                "handicap": im.handicap,
                "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
                "best_pick": best_pick,
                "actual_outcome": match.actual_outcome.value if hasattr(match.actual_outcome, "value") else str(match.actual_outcome) if match.actual_outcome else None,
                "actual_home_goals": match.actual_home_goals,
                "actual_away_goals": match.actual_away_goals,
            }

            # 计算正确性：仅使用 SPF 玩法与赛果比较
            spf_pred = None
            for p in preds:
                ptype = p.play_type.value if hasattr(p.play_type, "value") else str(p.play_type)
                if ptype == "SPF":
                    spf_pred = p
                    break

            if match.actual_outcome == "abandoned":
                mr["correct"] = None
                mr["actual_outcome"] = None
            elif match.actual_outcome and spf_pred:
                probs = spf_pred.probabilities or {}
                # 取 SPF 预测中概率最高的选项
                best_sel = max(probs, key=probs.get) if probs else None
                actual = match.actual_outcome.value if hasattr(match.actual_outcome, "value") else str(match.actual_outcome)
                mr["correct"] = best_sel == actual
                # 更新 best_pick 为 SPF 预测，用于报告展示
                if best_sel:
                    best_pick = {
                        "play_type": "SPF",
                        "selection": best_sel,
                        "probability": probs[best_sel],
                    }
                    mr["best_pick"] = best_pick
            elif match.actual_outcome and best_pick:
                # 兼容旧逻辑：如果不是 SPF，标记为 None (不计算命中率)
                mr["correct"] = None

            matches_report.append(mr)

        raw = issue.verification
        verified = {}
        if isinstance(raw, dict):
            verified = raw
        elif isinstance(raw, str):
            import json
            try:
                parsed = json.loads(raw)
                verified = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                pass
        total = len(issue.issue_matches or [])
        valid_matches = [m for m in matches_report if m.get("correct") is not None or m.get("actual_outcome") is not None]

        # 计算SPF命中率 — 优先用验证数据, 否则从matches_report实时计算
        spf_hits = verified.get("spf_hits", 0)
        if spf_hits == 0 and total > 0:
            spf_hits = sum(1 for m in matches_report if m.get("correct") is True)
        valid_total = sum(1 for m in matches_report if m.get("correct") is not None)
        accuracy = spf_hits / valid_total if valid_total > 0 else 0

        r9_hits = verified.get("r9_hits", 0)

        analysis = _build_issue_analysis(issue, matches_report, spf_hits, total, accuracy)

        reports.append({
            "issue_id": issue.issue_id,
            "issue_type": issue.issue_type,
            "status": issue.status,
            "sale_end": issue.sale_end.isoformat() if issue.sale_end else None,
            "draw_at": issue.draw_at.isoformat() if issue.draw_at else None,
            "total_matches": total,
            "spf_hits": spf_hits,
            "accuracy": accuracy,
            "r9_hits": r9_hits,
            "analysis": analysis,
            "matches": matches_report,
        })

    return {"reports": reports}


def _build_issue_analysis(issue, matches_report, spf_hits, total, accuracy):
    """构建自分析文本"""
    has_results = any(m.get("actual_outcome") for m in matches_report)
    if not has_results:
        return "待开奖"

    if issue.status == "on_sale":
        return "在售中"

    if total == 0:
        return "无比赛数据"

    parts = [f"命中 {spf_hits}/{total} ({accuracy:.1%})"]

    if accuracy >= 0.60:
        parts.append("表现优秀，模型预测准确率高于基准")
    elif accuracy >= 0.45:
        parts.append("表现正常，接近历史平均水准")
    else:
        parts.append("表现偏差，需关注该期赛事特征")

    wrong_matches = [m for m in matches_report if m.get("correct") is False]
    high_conf_miss = [m for m in wrong_matches if m.get("best_pick", {}).get("probability", 0) >= 0.55]

    if high_conf_miss:
        parts.append(f"高置信度未命中 {len(high_conf_miss)} 场，可能存在系统性偏差")

    correct_matches = [m for m in matches_report if m.get("correct") is True]
    low_conf_hit = [m for m in correct_matches if m.get("best_pick", {}).get("probability", 0) < 0.45]

    if low_conf_hit:
        parts.append(f"低置信度命中 {len(low_conf_hit)} 场，含运气成分")

    return "；".join(parts)


# ────────────────────────────
# Admin: 赔率采集管理
# ────────────────────────────
@app.post("/api/admin/odds/refresh")
def admin_refresh_all_odds(
    authorized: bool = Depends(_verify_admin_key),
    db: Session = Depends(get_db),
):
    """
    手动触发全部 upcoming 比赛的赔率刷新。
    优先级: BetExplorer 爬虫 > football-data > 合成赔率兜底。
    """
    from odds_collector import _get_upcoming_matches
    matches = _get_upcoming_matches(db, hours=72)
    if not matches:
        return {"status": "ok", "message": "No upcoming matches", "updated": 0}

    collector = OddsCollector(db)
    result = collector.collect_tier1_primary(matches)

    return {
        "status": "ok",
        "total_matches": result.get("total_matches", 0),
        "updated": result.get("updated_count", 0),
        "stale": result.get("stale_matches", 0),
        "budget_remaining": result.get("budget_remaining", 0),
        "message": f"Refreshed {result.get('updated_count', 0)}/{len(matches)} matches",
    }


@app.post("/api/admin/odds/refresh/{match_id}")
def admin_refresh_match_odds(
    match_id: int,
    authorized: bool = Depends(_verify_admin_key),
    db: Session = Depends(get_db),
):
    """手动触发单场比赛的赔率刷新。"""
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    collector = OddsCollector(db)
    sources = collector.collect_for_match(match)
    if sources:
        collector.update_match_primary_odds(match, sources)
        source_names = list(sources.keys())
        return {
            "status": "ok",
            "match_id": match_id,
            "match_code": match.match_code,
            "sources": source_names,
            "odds_home": match.odds_home,
            "odds_draw": match.odds_draw,
            "odds_away": match.odds_away,
        }
    else:
        return {
            "status": "failed",
            "match_id": match_id,
            "message": "No odds data available from any source",
        }


@app.get("/api/admin/odds/status")
def admin_odds_status(
    authorized: bool = Depends(_verify_admin_key),
    db: Session = Depends(get_db),
):
    """查看当前赔率数据覆盖情况。"""
    total = db.query(Match).count()
    with_odds = db.query(Match).filter(
        Match.odds_home.isnot(None),
        Match.odds_draw.isnot(None),
        Match.odds_away.isnot(None),
    ).count()
    without_odds = total - with_odds

    return {
        "total_matches": total,
        "with_odds": with_odds,
        "without_odds": without_odds,
        "coverage_pct": round(with_odds / total * 100, 1) if total > 0 else 0,
    }


# ────────────────────────────
# Health Check
# ────────────────────────────
@app.get("/api/health", response_model=HealthCheck)
def health(db: Session = Depends(get_db)):
    checks = {"status": "ok", "version": "0.1.0", "checks": {}}

    #数据库
    try:
        db.execute(text("SELECT 1"))
        checks["checks"]["database"] = "ok"
    except Exception as e:
        checks["checks"]["database"] = f"error: {e}"
        checks["status"] = "degraded"

    #调度器
    try:
        from scheduler import scheduler
        checks["checks"]["scheduler"] = "running" if scheduler.running else "stopped"
        if not scheduler.running:
            checks["status"] = "degraded"
    except Exception:
        checks["checks"]["scheduler"] = "unknown"

    #赔率新鲜度
    try:
        from models import MatchBookmakerOdds
        from datetime import datetime, timezone as _tz2
        latest = db.query(MatchBookmakerOdds).order_by(MatchBookmakerOdds.id.desc()).first()
        if latest:
            updated_at = getattr(latest, 'updated_at', None) or getattr(latest, 'created_at', None)
            if updated_at:
                age_hours = (datetime.now(_tz2.utc) - updated_at).total_seconds() / 3600
                checks["checks"]["odds_freshness"] = f"{age_hours:.1f}h"
                if age_hours > 24:
                    checks["status"] = "degraded"
            else:
                checks["checks"]["odds_freshness"] = "no_timestamp"
        else:
            checks["checks"]["odds_freshness"] = "no_odds"
    except Exception as e:
        checks["checks"]["odds_freshness"] = f"error: {e}"

    #活跃告警
    try:
        from alert_manager import get_active_alerts
        active = get_active_alerts()
        critical = [a for a in active if a.get("level") == "critical"]
        checks["checks"]["alerts"] = f"{len(active)} active ({len(critical)} critical)"
        if critical:
            checks["status"] = "degraded"
    except Exception:
        checks["checks"]["alerts"] = "unknown"

    return checks


@app.get("/api/health/detailed")
def health_detailed():
    """详细健康报告（来自 HealthDaemon 自检引擎）"""
    from health_daemon import get_latest_health
    return get_latest_health()


@app.get("/api/audit/reports")
def audit_reports(n: int = 7):
    """获取最近 N 天的模型复盘报告"""
    from model_audit import ModelAuditor
    return {"reports": ModelAuditor.get_latest_reports(n)}

# ────────────────────────────
# 用户设置
# ────────────────────────────
@app.get("/api/settings", response_model=SettingsResponse)
def get_user_settings(
    user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    from models import UserSettings
    s = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)
        db.commit()
        db.refresh(s)
    return {
        "risk_tier": s.risk_tier,
        "default_play_type": s.default_play_type,
        "show_ev": s.show_ev,
        "show_probability": s.show_probability,
        "notify_odds_change": s.notify_odds_change,
        "notify_match_start": s.notify_match_start,
    }


@app.put("/api/settings", response_model=SettingsUpdateResponse)
def update_user_settings(
    risk_tier: Optional[str] = None,
    default_play_type: Optional[str] = None,
    show_ev: Optional[bool] = None,
    show_probability: Optional[bool] = None,
    notify_odds_change: Optional[bool] = None,
    notify_match_start: Optional[bool] = None,
    user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    from models import UserSettings
    valid_tiers = {"conservative", "balanced", "aggressive", "speculative"}
    valid_plays = {"spf", "rq", "score", "goals", "half"}

    if risk_tier is not None and risk_tier not in valid_tiers:
        raise HTTPException(400, f"无效风险等级，可选: {', '.join(valid_tiers)}")
    if default_play_type is not None and default_play_type not in valid_plays:
        raise HTTPException(400, f"无效玩法，可选: {', '.join(valid_plays)}")

    s = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)

    if risk_tier is not None:
        s.risk_tier = risk_tier
    if default_play_type is not None:
        s.default_play_type = default_play_type
    if show_ev is not None:
        s.show_ev = show_ev
    if show_probability is not None:
        s.show_probability = show_probability
    if notify_odds_change is not None:
        s.notify_odds_change = notify_odds_change
    if notify_match_start is not None:
        s.notify_match_start = notify_match_start

    db.commit()
    return {"status": "updated"}


# ────────────────────────────
# 预测神经网络
# ────────────────────────────
@app.get("/api/bet-nn/status", response_model=BetNNStatusResponse)
def bet_nn_status():
    """预测神经网络训练状态"""
    from bet_nn import BetNetPredictor
    predictor = BetNetPredictor()
    return predictor.get_training_status()


@app.get("/api/bet-nn/predict/{match_id}", response_model=BetNNPredictResponse)
def bet_nn_predict(match_id: int):
    """对单场比赛推理预测评分"""
    from bet_nn import BetNetPredictor
    predictor = BetNetPredictor()
    if not predictor.is_ready():
        return {"ready": False, "message": "模型尚未训练，请等待自动训练或手动触发"}
    result = predictor.predict_from_db(match_id)
    if not result:
        raise HTTPException(404, "比赛不存在或无预测数据")
    return result


@app.post("/api/bet-nn/train", response_model=BetNNTrainResponse)
@limiter.limit("2/hour")
def bet_nn_train(request: Request, _: bool = Depends(_verify_admin_key)):
    """手动触发预测网络训练（仅管理员）"""
    from bet_nn import BetNetTrainer
    trainer = BetNetTrainer()
    result = trainer.train()
    if not result:
        return {"status": "skipped", "message": "训练样本不足"}
    return {"status": "trained", **result}


# ────────────────────────────
# 子模型状态 & 训练 & 综合报告
# ────────────────────────────
@app.get("/api/sub-models/status")
def sub_models_status():
    """所有子模型训练状态"""
    result = {}
    for name, module_path in [
        ("halftime", "sub_model_halftime"),
        ("score", "sub_model_score"),
        ("handicap", "sub_model_handicap"),
    ]:
        try:
            mod = __import__(module_path)
            cls = getattr(mod, f"{name.capitalize()}Predictor", None) or \
                  getattr(mod, {"halftime": "HalftimePredictor", "score": "ScorePredictor", "handicap": "HandicapPredictor"}[name])
            predictor = cls()
            result[name] = predictor.get_status()
        except Exception as e:
            result[name] = {"trained": False, "ready": False, "error": str(e)}
    return result


@app.post("/api/sub-models/train/{model_name}")
@limiter.limit("2/hour")
def sub_model_train(request: Request, model_name: str, _: bool = Depends(_verify_admin_key)):
    """手动触发子模型训练（仅管理员）"""
    trainers = {
        "halftime": ("sub_model_halftime", "HalftimeTrainer"),
        "score": ("sub_model_score", "ScoreTrainer"),
        "handicap": ("sub_model_handicap", "HandicapTrainer"),
    }
    if model_name not in trainers:
        raise HTTPException(400, f"Unknown model: {model_name}. Available: {list(trainers.keys())}")

    module_path, class_name = trainers[model_name]
    try:
        mod = __import__(module_path)
        trainer_cls = getattr(mod, class_name)
        trainer = trainer_cls()
        result = trainer.train()
        if not result:
            return {"status": "skipped", "model": model_name, "message": "训练样本不足"}
        return {"status": "trained", "model": model_name, **result}
    except Exception as e:
        raise HTTPException(500, f"训练失败: {e}")


@app.get("/api/predictions/{match_id}/report")
def prediction_report(match_id: int):
    """综合预测报告：主模型 + NN + 子模型"""
    from prediction_report import generate_report, report_to_dict
    report = generate_report(match_id)
    if not report or not report.ready:
        raise HTTPException(404, f"Match {match_id} not found or prediction unavailable")
    return report_to_dict(report)


@app.post("/api/sub-models/train-all")
@limiter.limit("1/hour")
def sub_model_train_all(request: Request, _: bool = Depends(_verify_admin_key)):
    """训练所有子模型（仅管理员）"""
    results = {}
    for name, (module_path, class_name) in [
        ("halftime", ("sub_model_halftime", "HalftimeTrainer")),
        ("score", ("sub_model_score", "ScoreTrainer")),
        ("handicap", ("sub_model_handicap", "HandicapTrainer")),
    ]:
        try:
            mod = __import__(module_path)
            trainer_cls = getattr(mod, class_name)
            trainer = trainer_cls()
            result = trainer.train()
            results[name] = {"status": "trained", **result} if result else {"status": "skipped"}
        except Exception as e:
            results[name] = {"status": "error", "message": str(e)}
    return results


# ────────────────────────────
# sporttery.cn 数据同步 API
# ────────────────────────────
@app.post("/api/strategy/optimize")
def trigger_param_optimize(
    max_combinations: int = 200,
    user: User = Depends(get_current_active_user),
):
    """手动触发参数寻优"""
    from param_optimizer import run_grid_search
    results = run_grid_search(max_combinations=max_combinations, sample_limit=5000)
    if not results:
        return {"status": "no_data", "message": "无回测数据或模型未就绪"}
    best = results[0]
    return {
        "status": "completed",
        "best_combined_roi": best.combined_roi,
        "best_high_count": best.high_count,
        "best_medium_count": best.medium_count,
        "best_skip_count": best.skip_count,
        "best_high_roi": best.high_roi,
        "best_medium_roi": best.medium_roi,
        "total_combinations_tested": len(results),
    }


@app.get("/api/strategy/monitor")
def get_strategy_monitor_status():
    """获取策略漂移监控状态 + 迭代计划"""
    from strategy_monitor import get_iteration_status, check_drift, load_baseline_snapshot
    status = get_iteration_status()
    baseline = load_baseline_snapshot()
    return {
        "iteration": status,
        "baseline_snapshot": baseline,
    }


@app.post("/api/strategy/monitor/check")
def trigger_drift_check(
    user: User = Depends(get_current_active_user),
):
    """手动触发漂移检测"""
    from strategy_monitor import check_drift, should_trigger_optimize
    triggered = check_drift()
    return {
        "triggered_count": len(triggered),
        "triggered": triggered,
        "should_optimize": should_trigger_optimize(triggered),
    }


@app.post("/api/strategy/nn-retrain-callback")
def trigger_nn_retrain_callback(
    user: User = Depends(get_current_active_user),
):
    """手动触发 NN 重训练回调（生成搜索空间 + 保存基线）"""
    from strategy_monitor import nn_retrain_callback
    return nn_retrain_callback()


@app.get("/api/strategy/iteration-plan")
def get_iteration_plan():
    """获取3轮迭代计划"""
    from strategy_monitor import ITERATION_PLANS
    return {
        "plans": [
            {
                "round": p.round_num,
                "title": p.title,
                "focus_params": list(p.focus_params),
                "goal": p.goal,
                "search_strategy": p.search_strategy,
                "expected_outcome": p.expected_outcome,
                "success_criteria": p.success_criteria,
            }
            for p in ITERATION_PLANS
        ],
    }


# ────────────────────────────
# 数据质量 & 清洗
# ────────────────────────────
@app.get("/api/admin/data-quality")
def data_quality_report(
    authorized: bool = Depends(_verify_admin_key),
    db: Session = Depends(get_db),
):
    """数据质量审计报告 (只读)。"""
    from data_cleaner import DataCleaner
    cleaner = DataCleaner(db)
    findings = cleaner.audit()
    return {
        "findings": [
            {
                "category": f.category,
                "severity": f.severity,
                "table": f.table,
                "count": f.count,
                "description": f.description,
                "fixable": f.fixable,
            }
            for f in findings
        ],
        "total_issues": len(findings),
        "critical_count": sum(1 for f in findings if f.severity == "critical"),
    }


@app.post("/api/admin/data-clean")
def data_clean(
    dry_run: bool = True,
    authorized: bool = Depends(_verify_admin_key),
    db: Session = Depends(get_db),
):
    """执行数据清洗。默认 dry_run=True 只预览。"""
    from data_cleaner import DataCleaner
    cleaner = DataCleaner(db)
    result = cleaner.clean(dry_run=dry_run)
    return {
        "dry_run": result.dry_run,
        "fixed": result.fixed,
        "errors": result.errors,
        "findings_count": len(result.findings),
        "findings_summary": [
            {"category": f.category, "severity": f.severity, "count": f.count}
            for f in result.findings
        ],
    }


# ─── SSE 实时推送 ───────────────────────────────
@app.get("/api/events")
async def sse_events():
    """SSE 端点：前端连接此端点接收实时推送"""
    from starlette.responses import StreamingResponse
    from sse import event_generator
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# ─── 智能串关推荐 ───────────────────────────────
@app.get("/api/jingcai/issues/{issue_id}/optimal-combo", response_model=OptimalComboResponse)
def get_optimal_combo(issue_id: int, top_n: int = 8, db: Session = Depends(get_db)):
    """获取当期最优串关推荐"""
    from optimal_combo import compute_optimal_combo
    picks = compute_optimal_combo(db, issue_id, top_n)
    return {"issue_id": issue_id, "picks": picks, "total": len(picks)}

if __name__ == "__main__":
    import uvicorn
    host = "127.0.0.1" if settings.DEBUG else "0.0.0.0"
    uvicorn.run("main:app", host=host, port=8000, reload=True)
