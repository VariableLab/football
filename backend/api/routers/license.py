from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database.models import User, get_db
from api.auth import get_current_active_user
from license_manager import redeem_license_key
from schemas import LicenseRedeem, LicenseRedeemOut
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/license", tags=["License"])
limiter = Limiter(key_func=get_remote_address)

@router.post("/redeem", response_model=LicenseRedeemOut)
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
