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
    from sqlalchemy import func
    from models import Match, Prediction, MatchStatus
    
    # 统计 SPF 准确率
    stats = db.query(
        func.count(Match.id).label("total"),
        func.sum(case((Match.actual_outcome == func.json_extract(Prediction.probabilities, '$.predicted'), 1), else_=0)).label("correct")
    ).join(
        Prediction, Prediction.match_id == Match.id
    ).filter(
        Match.status == MatchStatus.FINISHED,
        Prediction.play_type == "SPF",
        Match.actual_outcome.isnot(None)
    ).first()
    
    # 注意：上面的 query 逻辑中 probabilities 存的是字典，sqlite 提取 max 可能需要更复杂的逻辑
    # 我们可以通过读取最近的 audit 报告来获取这些信息，这更高效。
    reports = ModelAuditor.get_latest_reports(30)
    if not reports:
        return {"error": "No audit data available"}
    
    history = []
    for r in reversed(reports):
        history.append({
            "date": r["date"],
            "accuracy": r["direction_accuracy"],
            "brier": r["brier_score"],
            "samples": r["total"]
        })
        
    return {
        "overall_history": history,
        "latest_brier": history[-1]["brier"] if history else None,
        "latest_accuracy": history[-1]["accuracy"] if history else None
    }

from sqlalchemy import case
