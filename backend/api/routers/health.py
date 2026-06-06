from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database.models import get_db
from schemas import HealthCheck
from api.auth import _verify_admin_key

router = APIRouter(prefix="/api", tags=["System"])

@router.get("/health", response_model=HealthCheck)
def health(db: Session = Depends(get_db)):
    checks = {"status": "ok", "version": "0.1.0", "checks": {}}

    try:
        db.execute(text("SELECT 1"))
        checks["checks"]["database"] = "ok"
    except Exception as e:
        checks["checks"]["database"] = f"error: {e}"
        checks["status"] = "degraded"

    try:
        from scheduler import scheduler
        checks["checks"]["scheduler"] = "running" if scheduler.running else "stopped"
        if not scheduler.running:
            checks["status"] = "degraded"
    except Exception:
        checks["checks"]["scheduler"] = "unknown"

    try:
        from database.models import MatchBookmakerOdds
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

@router.get("/health/detailed")
def health_detailed():
    """详细健康报告"""
    from health_daemon import get_latest_health
    return get_latest_health()

@router.get("/audit/reports")
def audit_reports(n: int = 7):
    """获取最近 N 天的模型复盘报告"""
    from model_audit import ModelAuditor
    return {"reports": ModelAuditor.get_latest_reports(n)}
