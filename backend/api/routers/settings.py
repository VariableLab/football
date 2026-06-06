from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional

from database.models import get_db, User, UserSettings
from api.auth import get_current_active_user
from schemas import SettingsResponse, SettingsUpdateResponse

router = APIRouter(prefix="/api/settings", tags=["Settings"])

@router.get("", response_model=SettingsResponse)
def get_user_settings(
    user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
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

@router.put("", response_model=SettingsUpdateResponse)
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
    valid_tiers = {"conservative", "balanced", "aggressive", "speculative"}
    valid_plays = {"spf", "rq", "score", "goals", "half"}

    if risk_tier is not None and risk_tier not in valid_tiers:
        raise HTTPException(400, f"无效风险等级")
    if default_play_type is not None and default_play_type not in valid_plays:
        raise HTTPException(400, f"无效玩法")

    s = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)

    if risk_tier is not None: s.risk_tier = risk_tier
    if default_play_type is not None: s.default_play_type = default_play_type
    if show_ev is not None: s.show_ev = show_ev
    if show_probability is not None: s.show_probability = show_probability
    if notify_odds_change is not None: s.notify_odds_change = notify_odds_change
    if notify_match_start is not None: s.notify_match_start = notify_match_start

    db.commit()
    return {"status": "updated"}
