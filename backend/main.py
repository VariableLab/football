# -*- coding: utf-8 -*-
import os
from pathlib import Path

from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from utils.logger import get_logger
from database.config import get_settings
from database.models import init_db, get_db, Team, Match, Prediction
from api.auth import (
    verify_admin_key
)
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
    logger.info("Application starting up", extra={"extra_data": {"version": "0.3.0"}})

    # 生产环境安全守卫
    if settings.DEBUG and os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError("DEBUG=True is not allowed in production")

    # 安全配置守卫
    if not settings.SECRET_KEY or settings.SECRET_KEY.startswith("fallback"):
        raise RuntimeError("SECRET_KEY must be set via environment variable (not .env fallback)")
    if not settings.ADMIN_API_KEY:
        raise RuntimeError("ADMIN_API_KEY must be set via environment variable")

    init_db()
    from monitor.scheduler import start_scheduler
    start_scheduler()
    yield
    from monitor.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("Application shutting down")

app = FastAPI(
    title=settings.APP_NAME,
    version="0.2.1-diag",
    lifespan=lifespan,
    docs_url=None if not settings.DEBUG else "/docs",
    redoc_url=None if not settings.DEBUG else "/redoc",
    openapi_url=None if not settings.DEBUG else "/openapi.json",
)

@app.get("/api/diag/db-stats")
def db_stats(
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """診斷：返回資料庫統計數據 (受管理員密鑰保護)"""
    return {
        "matches": db.query(Match).count(),
        "teams": db.query(Team).count(),
        "predictions": db.query(Prediction).count(),
        "upcoming_matches": db.query(Match).filter(Match.status != "finished").count(),
        "db_url_redacted": str(settings.DATABASE_URL).split("@")[-1] if "@" in str(settings.DATABASE_URL) else "local/sqlite"
    }

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

# CORS — 生产环境指定域名,开发环境允许全部
_ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000,https://football.nett.to")
ALLOWED_ORIGINS = [o.strip() for o in _ALLOWED_ORIGINS_RAW.split(",")]

# 如果明确配置了通配符(不推荐),则关闭 credentials
if "*" in ALLOWED_ORIGINS:
    cors_credentials = False
    logger.warning("[cors] Wildcard origin detected — credentials disabled for security")
else:
    cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key", "X-Request-ID"],
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

# ─── Legacy 路径纯重定向(Phase 2.G: 取代 api/compat_routes.py)───────────────
# 旧的 compat router 同时塞了 15 个 shim,其中 11 个只是把请求转发到业务路由,
# 既增加了进程调用栈,又在 OpenAPI 里暴露重复地址。规范做法:
#   - 真正的路径改名:用 307 在应用层做轻量重定向(以下 4 项)。
#   - 同名同义:不重定向,让前端切到 /api/matches/{id},因为它们已经存在。

def _redirect(path: str):
    """构造一个 307 Temporary Redirect 端点(轻量、无依赖、include_in_schema=False)。"""
    async def _handler():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(path, status_code=307)
    return _handler

app.add_api_route("/api/live-odds",                _redirect("/api/live/live-odds"),          methods=["GET"], include_in_schema=False)
app.add_api_route("/api/live-odds/{match_id}",     _redirect("/api/live/live-odds/{match_id}"), methods=["GET"], include_in_schema=False)
app.add_api_route("/api/monitor/health",           _redirect("/api/health"),                   methods=["GET"], include_in_schema=False)
app.add_api_route("/api/admin/dashboard",          _redirect("/api/admin/data-quality"),       methods=["GET"], include_in_schema=False)
logger.info("[compat] Legacy 重定向已挂载(307,4 条)")

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
