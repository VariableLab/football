from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
import secrets as _secrets

from database.models import User, UserQuantProfile, get_db
from api.auth import (
    get_password_hash, verify_password, create_access_token, 
    get_current_active_user
)
from schemas import UserRegister, UserLogin, UserOut, Token
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/auth", tags=["Auth"])
limiter = Limiter(key_func=get_remote_address)

@router.post("/register", response_model=Token)
@limiter.limit("5/hour")
def register(request: Request, data: UserRegister, db: Session = Depends(get_db)):
    """Register a new user."""
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        # Return same response format to prevent user enumeration
        fake_token = f"enum.{_secrets.token_urlsafe(32)}"
        return {"access_token": fake_token}

    user = User(
        email=data.email,
        password_hash=get_password_hash(data.password)
    )
    db.add(user)
    db.flush() # Get user.id

    # Create default Quant Profile for Phase 2 personalization
    profile = UserQuantProfile(
        user_id=user.id,
        risk_tolerance="balanced",
        base_bankroll=1000.0,
        preferred_leagues=[]
    )
    db.add(profile)
    
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@router.post("/login", response_model=Token)
@limiter.limit("10/hour")
def login(request: Request, data: UserLogin, db: Session = Depends(get_db)):
    """Login and get access token."""
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_active_user)):
    """Get current user info."""
    return current_user
