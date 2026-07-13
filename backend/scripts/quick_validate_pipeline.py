"""Quick pipeline validation — test the simplified pipeline on finished matches.

Compares old vs new prediction engine behavior by running predictions on
recent finished matches and checking accuracy.

Usage:
    cd backend && PYTHONPATH=. ./venv/bin/python scripts/quick_validate_pipeline.py
"""
import os, sys, json, time

import numpy as np

from database.models import SessionLocal, Match, MatchStatus, Prediction
from core.prediction_engine import PredictionEngine, build_context_from_match
from core.prediction_fusion import EnsembleFusion
from utils.logger import get_logger

logger = get_logger("quick_validate")


def spf_accuracy(predictions, matches):
    """Calculate SPF accuracy from predictions."""
    total = correct = 0
    brier_sum = 0.0

    for pred in predictions:
        match = matches.get(pred.match_id)
        if not match or match.actual_outcome is None:
            continue

        probs = pred.probabilities
        if isinstance(probs, str):
            probs = json.loads(probs)

        predicted = max(probs, key=probs.get)
        actual = match.actual_outcome

        total += 1
        if predicted == actual:
            correct += 1

        # Brier score
        for outcome in ["home", "draw", "away"]:
            expected = 1.0 if outcome == actual else 0.0
            brier_sum += (probs.get(outcome, 0) - expected) ** 2

    acc = correct / total if total > 0 else 0
    brier = brier_sum / (total * 3) if total > 0 else 0
    return total, correct, acc, brier


def main():
    db = SessionLocal()

    # Get recent finished matches (last 3000 for speed)
    matches = (
        db.query(Match)
        .filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
            Match.closing_odds_home.isnot(None),
        )
        .order_by(Match.kickoff_at.desc())
        .limit(3000)
        .all()
    )
    logger.info(f"Testing on {len(matches)} recent finished matches")

    match_dict = {m.id: m for m in matches}

    # Test each match
    total = correct = 0
    brier_sum = 0.0
    errors = 0

    t0 = time.time()
    for i, match in enumerate(matches):
        if i % 500 == 0:
            logger.info(f"Processing: {i}/{len(matches)} ({time.time()-t0:.0f}s)")
        try:
            ctx = build_context_from_match(match)
            engine = PredictionEngine(db_session=db)
            result = engine.predict(ctx)

            probs = result.spf
            predicted = max(probs, key=probs.get)
            actual = match.actual_outcome

            total += 1
            if predicted == actual:
                correct += 1

            for outcome in ["home", "draw", "away"]:
                expected = 1.0 if outcome == actual else 0.0
                brier_sum += (probs.get(outcome, 0) - expected) ** 2

        except Exception as e:
            errors += 1
            if errors < 5:
                logger.warning(f"Error on match {match.id}: {e}")

    elapsed = time.time() - t0
    acc = correct / total if total > 0 else 0
    brier = brier_sum / (total * 3) if total > 0 else 0

    print("\n" + "=" * 60)
    print("  Pipeline Simplification Validation")
    print("=" * 60)
    print(f"  Matches tested: {total}")
    print(f"  Errors: {errors}")
    print(f"  Time: {elapsed:.0f}s ({elapsed/max(total,1):.1f}s/match)")
    print(f"  SPF Accuracy: {correct}/{total} = {acc:.1%}")
    print(f"  Brier Score:  {brier:.4f}")
    print(f"  Avg max prob: {np.mean([max(result.spf.values()) for result in []]):.4f}")
    print("=" * 60)

    db.close()


if __name__ == "__main__":
    main()
