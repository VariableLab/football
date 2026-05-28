from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from models import get_db
from model_audit import ModelAuditor, run_self_heal_cycle
from auth import get_current_active_user, User

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

@router.get("/reports")
def get_audit_reports(n: int = 14, user: User = Depends(get_current_active_user)):
    """获取最近 N 天的模型审计报告"""
    return ModelAuditor.get_latest_reports(n)

@router.post("/run-audit")
def trigger_daily_audit(days_back: int = 1, user: User = Depends(get_current_active_user)):
    """手动触发模型审计"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    
    auditor = ModelAuditor()
    report = auditor.run_daily_audit(days_back=days_back)
    if not report:
        return {"status": "no_data", "message": "No finished matches found for the period"}
    
    return {"status": "success", "report_date": report.date, "total": report.total}

@router.get("/self-heal/status")
def get_self_heal_status(user: User = Depends(get_current_active_user)):
    """查看自愈任务状态"""
    from model_audit import _load_self_heal_state
    return _load_self_heal_state()

@router.post("/self-heal/trigger")
def trigger_self_heal(reason: str = "manual_api", user: User = Depends(get_current_active_user)):
    """手动触发自愈闭环（慎用：消耗计算资源且会更新生产权重）"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    
    # 在后台运行，防止 API 超时
    import threading
    thread = threading.Thread(target=run_self_heal_cycle, args=(reason,))
    thread.start()
    
    return {"status": "triggered", "message": "Self-heal cycle started in background"}

@router.get("/accuracy-stats")
def get_accuracy_stats(db: Session = Depends(get_db)):
    """获取整体准确率统计（用于看板）"""
    from core.prediction_engine import PredictionEngine
    from monitor.model_audit import ModelAuditor
    
    reports = ModelAuditor.get_latest_reports(30)
    
    history = []
    for r in reversed(reports):
        history.append({
            "date": r["date"],
            "accuracy": r["direction_accuracy"],
            "brier": r["brier_score"],
            "samples": r["total"]
        })
        
    # 获取当前模型维度
    engine = PredictionEngine(db_session=db)
    # 尝试加载全局权重以确定维度
    model_dim = 0
    lr_active = False
    if engine._lr_weights:
        model_dim = engine._lr_weights.coef_home.shape[0]
        lr_active = True
        
    return {
        "overall_history": history,
        "latest_brier": history[-1]["brier"] if history else None,
        "latest_accuracy": history[-1]["accuracy"] if history else None,
        "model_dimension": model_dim,
        "is_lr_enabled": lr_active
    }


from sqlalchemy import case
