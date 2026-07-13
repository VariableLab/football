import hmac
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from database.config import get_settings
from database.models import (
    get_db, Team, Match, MatchStatus, Prediction, AuditLog, User, LicenseKey
)
from api.schemas import (
    TeamCreate, TeamOut, MatchCreate, MatchOut, MatchUpdateResult,
    PredictionCreate, PredictionOut, DashboardStats, LicenseKeyCreate
)
from monitor.validation_engine import ValidationEngine, MatchValidator

settings = get_settings()
router = APIRouter(prefix="/api/admin", tags=["Admin (OpenClaw)"])


def verify_admin_key(x_api_key: str = Header(..., alias="X-Api-Key")):
    if not hmac.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid admin API key")
    return True


# ────────────────────────────
# Dashboard
# ────────────────────────────
@router.get("/dashboard", response_model=DashboardStats)
def admin_dashboard(
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Get system overview stats."""
    total_matches = db.query(Match).count()
    finished = db.query(Match).filter(Match.status == MatchStatus.FINISHED).count()
    total_preds = db.query(Prediction).count()
    total_users = db.query(User).count()
    paid_users = db.query(User).filter(User.is_paid == True).count()
    
    # Simple accuracy: predictions where match finished
    # (In reality you'd calculate per-play-type)
    accuracy = 0.0
    if finished > 0:
        # Placeholder: real implementation compares prediction vs actual
        accuracy = 0.0
    
    return DashboardStats(
        total_matches=total_matches,
        finished_matches=finished,
        total_predictions=total_preds,
        prediction_accuracy=accuracy,
        total_users=total_users,
        paid_users=paid_users
    )


# ────────────────────────────
# Team Management
# ────────────────────────────
@router.post("/teams", response_model=TeamOut)
def create_team(
    data: TeamCreate,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Create a new team."""
    team = Team(**data.dict())
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("/teams", response_model=List[TeamOut])
def list_teams(
    group: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """List all teams. Filter by group if provided."""
    q = db.query(Team)
    if group:
        q = q.filter(Team.group_name == group.upper())
    return q.all()


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


# ────────────────────────────
# Match Management
# ────────────────────────────
@router.post("/matches", response_model=MatchOut)
def create_match(
    data: MatchCreate,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Create a new match. Use this when schedule is released."""
    # Validate teams exist
    home = db.query(Team).filter(Team.id == data.home_team_id).first()
    away = db.query(Team).filter(Team.id == data.away_team_id).first()
    if not home or not away:
        raise HTTPException(status_code=400, detail="Team not found")
    
    match = Match(**data.dict(), status=MatchStatus.SCHEDULED)
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


@router.get("/matches", response_model=List[MatchOut])
def list_matches(
    status: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """List matches with optional filters."""
    q = db.query(Match)
    if status:
        q = q.filter(Match.status == status)
    if group:
        q = q.filter(Match.group_name == group.upper())
    if stage:
        q = q.filter(Match.stage == stage)
    return q.order_by(Match.kickoff_at).all()


@router.get("/matches/{match_id}", response_model=MatchOut)
def get_match(
    match_id: int,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.patch("/matches/{match_id}/result")
def update_match_result(
    match_id: int,
    data: MatchUpdateResult,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """
    Update match result. Call this when a match finishes.
    Determines actual_outcome automatically.
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    match.actual_home_goals = data.actual_home_goals
    match.actual_away_goals = data.actual_away_goals
    
    if data.actual_home_goals > data.actual_away_goals:
        match.actual_outcome = "home"
    elif data.actual_home_goals < data.actual_away_goals:
        match.actual_outcome = "away"
    else:
        match.actual_outcome = "draw"
    
    match.status = MatchStatus.FINISHED
    match.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "message": "Result updated",
        "match_id": match_id,
        "score": f"{data.actual_home_goals}:{data.actual_away_goals}",
        "outcome": match.actual_outcome
    }


@router.patch("/matches/{match_id}/odds")
def update_match_odds(
    match_id: int,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Update match odds snapshot."""
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    match.odds_home = odds_home
    match.odds_draw = odds_draw
    match.odds_away = odds_away
    db.commit()
    return {"message": "Odds updated"}


# ────────────────────────────
# Prediction Management
# ────────────────────────────
@router.post("/predictions", response_model=PredictionOut)
def create_prediction(
    data: PredictionCreate,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """
    Lock a prediction snapshot before match starts.
    This creates an immutable record for post-match validation.
    """
    match = db.query(Match).filter(Match.id == data.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    pred = Prediction(**data.dict())
    db.add(pred)
    db.commit()
    db.refresh(pred)
    return pred


@router.get("/predictions", response_model=List[PredictionOut])
def list_predictions(
    match_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    q = db.query(Prediction)
    if match_id:
        q = q.filter(Prediction.match_id == match_id)
    return q.all()


# ────────────────────────────
# License Key Management
# ────────────────────────────
@router.post("/licenses/generate")
def generate_licenses(
    data: LicenseKeyCreate,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Generate batch license keys."""
    from core.license_manager import create_license_keys
    from database.models import LicenseType
    
    lt = LicenseType.MATCH if data.license_type == "match" else LicenseType.TOURNAMENT
    keys = create_license_keys(
        db=db,
        license_type=lt,
        count=data.count,
        match_id=data.match_id
    )
    return {
        "generated": len(keys),
        "keys": [{"key": k.key, "type": k.license_type.value} for k in keys]
    }


@router.get("/licenses")
def list_licenses(
    used: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """List license keys with optional filter."""
    q = db.query(LicenseKey)
    if used is not None:
        q = q.filter(LicenseKey.is_used == used)
    return q.all()


# ────────────────────────────
# Validation (实时验证)
# ────────────────────────────
@router.get("/validation")
def run_validation(
    match_type: Optional[str] = Query(None, description="Filter by match_type: world_cup / friendly / warm_up"),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """
    Run post-match validation on finished matches.
    Compares locked predictions against actual results.
    """
    report = ValidationEngine.run_validation(db, match_type=match_type)
    return report.to_dict()


@router.get("/validation/friendly")
def validate_friendly(
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Quick endpoint: validate only friendly/warm-up matches."""
    report = ValidationEngine.validate_friendly_only(db)
    return report.to_dict()


@router.get("/validation/matches/{match_id}")
def validate_single_match(
    match_id: int,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Validate a single finished match."""
    result = MatchValidator.validate_match(db, match_id)
    if not result:
        raise HTTPException(status_code=404, detail="Match not found or not finished / no predictions")
    return result.to_dict()


# ────────────────────────────
# Audit Log
# ────────────────────────────
@router.get("/audit-logs")
def list_audit_logs(
    match_id: Optional[int] = Query(None),
    data_type: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_key)
):
    """Query audit logs."""
    q = db.query(AuditLog)
    if match_id:
        q = q.filter(AuditLog.match_id == match_id)
    if data_type:
        q = q.filter(AuditLog.data_type == data_type)
    return q.order_by(AuditLog.ingest_timestamp.desc()).limit(limit).all()
