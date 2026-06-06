from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any

from database.models import get_db
from schemas import (
    BetNNStatusResponse, BetNNPredictResponse, BetNNTrainResponse
)
from api.auth import _verify_admin_key
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api", tags=["Models"])
limiter = Limiter(key_func=get_remote_address)

# ────────────────────────────
# BetNN
# ────────────────────────────
@router.get("/bet-nn/status", response_model=BetNNStatusResponse)
def bet_nn_status():
    return {"ready": False, "message": "BetNN is deprecated, use StackingPredictor"}

@router.get("/bet-nn/predict/{match_id}", response_model=BetNNPredictResponse)
def bet_nn_predict(match_id: int):
    return {"ready": False, "message": "模型尚未训练"}

@router.post("/bet-nn/train", response_model=BetNNTrainResponse)
@limiter.limit("2/hour")
def bet_nn_train(request: Request, _: bool = Depends(_verify_admin_key)):
    return {"status": "skipped", "message": "训练样本不足"}

# ────────────────────────────
# Sub Models
# ────────────────────────────
@router.get("/sub-models/status")
def sub_models_status():
    result = {}
    for name, module_path in [
        ("halftime", "core.sub_model_halftime"), # Fix paths if needed
        ("score", "core.sub_model_score"),
        ("handicap", "core.sub_model_handicap"),
    ]:
        # Implementation depends on how modules are structured
        result[name] = {"trained": False, "ready": False}
    return result

@router.post("/sub-models/train/{model_name}")
@limiter.limit("2/hour")
def sub_model_train(request: Request, model_name: str, _: bool = Depends(_verify_admin_key)):
    return {"status": "skipped", "model": model_name}

@router.get("/predictions/{match_id}/report")
def prediction_report(match_id: int):
    from core.prediction_report import generate_report, report_to_dict
    report = generate_report(match_id)
    if not report or not report.ready:
        raise HTTPException(404, f"Match {match_id} not found")
    return report_to_dict(report)
