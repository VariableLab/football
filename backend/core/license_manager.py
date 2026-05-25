import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from models import LicenseKey, LicenseRedemption, User, LicenseType


ALPHABET = string.ascii_uppercase + string.digits


def generate_license_key(length: int = 24) -> str:
    """Generate a random license key like: WC26-XXXX-XXXX-XXXX-XXXX"""
    raw = ''.join(secrets.choice(ALPHABET) for _ in range(length))
    return f"WC26-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def create_license_keys(
    db: Session,
    license_type: LicenseType,
    count: int = 1,
    match_id: Optional[int] = None,
    tournament: str = "WC2026"
) -> List[LicenseKey]:
    """Batch create unused license keys."""
    keys = []
    for _ in range(count):
        for _attempt in range(10):
            key_str = generate_license_key()
            existing = db.query(LicenseKey).filter(LicenseKey.key == key_str).first()
            if not existing:
                break
        else:
            raise RuntimeError("Failed to generate unique license key after 10 attempts")

        lk = LicenseKey(
            key=key_str,
            license_type=license_type,
            match_id=match_id,
            tournament=tournament,
            is_used=False
        )
        db.add(lk)
        keys.append(lk)
    db.commit()
    for k in keys:
        db.refresh(k)
    return keys


def redeem_license_key(db: Session, user: User, key: str) -> Optional[LicenseRedemption]:
    """Redeem a license key for a user. Atomic key claim + row-level lock on user."""
    # Step 1: Atomic claim of the key (prevents double-redeem)
    result = db.execute(
        update(LicenseKey)
        .where(LicenseKey.key == key, LicenseKey.is_used == False)
        .values(is_used=True, used_by=user.id, used_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        return None

    lk = db.query(LicenseKey).filter(LicenseKey.key == key).first()

    # Step 2: Lock user row with FOR UPDATE to prevent concurrent paid_until races
    locked_user = db.query(User).filter(User.id == user.id).with_for_update().first()
    if not locked_user:
        db.rollback()
        return None

    now = datetime.now(timezone.utc)

    if lk.license_type == LicenseType.TOURNAMENT:
        locked_user.is_paid = True
        locked_user.paid_until = now + timedelta(days=60)
    elif lk.license_type == LicenseType.MATCH:
        locked_user.is_paid = True
        locked_user.paid_until = max(
            locked_user.paid_until or datetime.min.replace(tzinfo=timezone.utc),
            now + timedelta(days=7),
        )

    # Record redemption
    redemption = LicenseRedemption(user_id=user.id, license_id=lk.id)
    db.add(redemption)
    db.commit()
    db.refresh(redemption)
    return redemption


def get_user_licenses(db: Session, user_id: int) -> List[LicenseKey]:
    """Get all license keys redeemed by a user."""
    redemptions = db.query(LicenseRedemption).filter(
        LicenseRedemption.user_id == user_id
    ).all()
    license_ids = [r.license_id for r in redemptions]
    return db.query(LicenseKey).filter(LicenseKey.id.in_(license_ids)).all()
