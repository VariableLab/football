import sys, os
_root = os.path.dirname(os.path.abspath(__file__))
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

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
from fastapi.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from contextlib import asynccontextmanager

from utils.logger import get_logger
from database.config import get_settings
from database.models import init_db, get_db, User, Team, Match, MatchStatus, Prediction, JingcaiIssue, JingcaiIssueMatch, OddsHistory
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
from api.admin import router as admin_router
from api.routers.matches import router as matches_router
from api.routers.feedback import router as feedback_router
from api.routers.monitor import router as monitor_router
from api.routers.advisor import router as advisor_router
from api.routers.auth import router as auth_router
from api.routers.license import router as license_router
from api.routers.jingcai import router as jingcai_router
from api.routers.live import router as live_router
from api.routers.public import router as public_router
from api.routers.validation import router as validation_router
from api.routers.admin_management import router as admin_mgmt_router
from api.routers.health import router as health_router
from api.routers.settings import router as settings_router
from api.routers.models import router as models_router
from api.routers.strategy import router as strategy_router
from api.routers.events import router as events_router
from api.routers.content import router as content_router
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

# CORS — 全放开以确保生产环境数据通畅
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
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
app.include_router(auth_router)
app.include_router(license_router)
app.include_router(jingcai_router)
app.include_router(live_router)
app.include_router(public_router)
app.include_router(validation_router)
app.include_router(admin_mgmt_router)
app.include_router(health_router)
app.include_router(settings_router)
app.include_router(models_router)
app.include_router(strategy_router)
app.include_router(events_router)
app.include_router(content_router)

# Static files — 使用绝对路径
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    """首页 — 返回前端入口"""
    return FileResponse(os.path.join(_root, "..", "static", "index.html"))


if __name__ == "__main__":
    import uvicorn
    host = "127.0.0.1" if settings.DEBUG else "0.0.0.0"
    uvicorn.run("main:app", host=host, port=8000, reload=True)
