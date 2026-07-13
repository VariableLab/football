"""Backfill confidence for predictions that were generated before confidence was added.

Usage:
    cd backend && PYTHONPATH=. ./venv/bin/python scripts/backfill_confidence.py
"""
import json
import sys
import os


import numpy as np
from database.models import SessionLocal, Prediction


def compute_confidence_for_probs(probabilities, confidence_hint=None):
    """Compute confidence string from probability dict.

    Uses entropy-based scoring:
    - high: low entropy (concentrated probability)
    - medium: moderate entropy
    - low: high entropy (flat distribution)
    """
    if isinstance(probabilities, str):
        probs = json.loads(probabilities)
    else:
        probs = probabilities

    values = [probs.get(k, 0) for k in ["home", "draw", "away", "主", "平", "客", "H", "D", "A"]]
    values = [v for v in values if v > 0]
    if not values:
        return "low"

    p = np.array(values, dtype=np.float64)
    p = p / p.sum()
    entropy = -np.sum(p * np.log(p + 1e-15))
    norm_entropy = entropy / 1.098  # ln(3)

    max_prob = np.max(p)

    if norm_entropy < 0.4 and max_prob > 0.55:
        return "high"
    if norm_entropy < 0.7 and max_prob > 0.40:
        return "medium"
    return "low"


def main():
    db = SessionLocal()
    try:
        null_preds = (
            db.query(Prediction)
            .filter(Prediction.confidence.is_(None))
            .limit(5000)
            .all()
        )
        print(f"Found {len(null_preds)} predictions with null confidence")

        updated = 0
        for pred in null_preds:
            if pred.probabilities:
                conf = compute_confidence_for_probs(pred.probabilities)
                pred.confidence = conf
                updated += 1

        db.commit()
        print(f"Backfilled confidence for {updated} predictions")
        print(f"Remaining null: {db.query(Prediction).filter(Prediction.confidence.is_(None)).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
