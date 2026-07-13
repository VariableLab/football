from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.config import get_settings
from database.models import get_db, Match
from ingestion.odds_collector import OddsCollector
from api.auth import verify_admin_key

settings = get_settings()
# 命名空间: 与 backend/api/admin.py 同属 /api/admin 大域,
# 以 /api/admin/mgmt/* 子前缀避开与 /api/admin/{teams,matches,...} 的路径冲突。
router = APIRouter(prefix="/api/admin/mgmt", tags=["Admin Mgmt"])

@router.post("/odds/refresh")
def admin_refresh_all_odds(
    authorized: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db),
):
    """手动触发全部 upcoming 比赛的赔率刷新。"""
    from ingestion.odds_collector import _get_upcoming_matches
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

@router.post("/odds/refresh/{match_id}")
def admin_refresh_match_odds(
    match_id: int,
    authorized: bool = Depends(verify_admin_key),
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

@router.get("/odds/status")
def admin_odds_status(
    authorized: bool = Depends(verify_admin_key),
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

@router.get("/data-quality")
def data_quality_report(
    authorized: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db),
):
    """数据质量审计报告 (只读)。"""
    from ingestion.data_cleaner import DataCleaner
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

@router.post("/data-clean")
def data_clean(
    dry_run: bool = True,
    authorized: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db),
):
    """执行数据清洗。"""
    from ingestion.data_cleaner import DataCleaner
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
