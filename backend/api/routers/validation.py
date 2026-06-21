from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.models import get_db
from schemas import ValidationReportResponse, CalibrationCurveResponse, PlayTypeBreakdownResponse
from validation_engine import ValidationEngine

router = APIRouter(prefix="/api/validation", tags=["Validation"])

@router.get("", response_model=ValidationReportResponse)
def public_validation(
    match_type: str = None,
    db: Session = Depends(get_db)
):
    """Public validation report."""
    report = ValidationEngine.run_validation(db, match_type=match_type)
    return report.to_dict()

@router.get("/calibration", response_model=CalibrationCurveResponse)
def calibration_curve(db: Session = Depends(get_db)):
    """Probability calibration curve."""
    return ValidationEngine.calibration_curve(db)

@router.get("/by-play-type", response_model=PlayTypeBreakdownResponse)
def validation_by_play_type(db: Session = Depends(get_db)):
    """Accuracy breakdown by play type."""
    return ValidationEngine.validate_by_play_type(db)
