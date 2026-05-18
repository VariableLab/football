"""Draw detection and probability calibration.

The base SPF model can assign reasonable draw probability while still never
ranking draw first. This module applies a small, auditable post-processing
step that can be tuned with walk-forward validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import math
import json
import os


OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class DrawCalibrationParams:
    enabled: bool = True
    elo_diff_threshold: float = 120.0
    market_draw_threshold: float = 0.26
    model_draw_threshold: float = 0.24
    xg_diff_threshold: float = 0.50
    min_signals: int = 2
    min_draw_prob: float = 0.18
    draw_boost: float = 1.30
    draw_cap: float = 0.45
    promote_draw: bool = False
    promote_min_signals: int = 3
    promote_margin: float = 0.005

    def to_dict(self) -> Dict[str, float | int | bool]:
        return asdict(self)


DEFAULT_DRAW_PARAMS = DrawCalibrationParams(enabled=False)


@dataclass(frozen=True)
class DrawFeatures:
    elo_diff: float
    xg_diff: float
    market_draw_prob: Optional[float] = None
    is_knockout: bool = False


def normalize_probs(probs: Dict[str, float], floor: float = 1e-6) -> Dict[str, float]:
    cleaned = {k: max(floor, float(probs.get(k, 0.0))) for k in OUTCOMES}
    total = sum(cleaned.values())
    if total <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {k: v / total for k, v in cleaned.items()}


def market_probabilities(
    odds_home: Optional[float],
    odds_draw: Optional[float],
    odds_away: Optional[float],
) -> Optional[Dict[str, float]]:
    odds = (odds_home, odds_draw, odds_away)
    if not all(v is not None and v > 1.01 for v in odds):
        return None
    raw = {"home": 1.0 / odds_home, "draw": 1.0 / odds_draw, "away": 1.0 / odds_away}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def load_draw_params(path: Optional[str] = None) -> DrawCalibrationParams:
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "data", "draw_calibration", "params.json")
    if not os.path.exists(path):
        return DEFAULT_DRAW_PARAMS
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        allowed = DrawCalibrationParams.__dataclass_fields__.keys()
        return DrawCalibrationParams(**{k: raw[k] for k in allowed if k in raw})
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return DEFAULT_DRAW_PARAMS


def draw_signal_count(
    probs: Dict[str, float],
    features: DrawFeatures,
    params: DrawCalibrationParams = DEFAULT_DRAW_PARAMS,
) -> int:
    signals = 0
    if abs(features.elo_diff) < params.elo_diff_threshold:
        signals += 1
    if (
        features.market_draw_prob is not None
        and features.market_draw_prob > params.market_draw_threshold
    ):
        signals += 1
    if probs.get("draw", 0.0) > params.model_draw_threshold:
        signals += 1
    if abs(features.xg_diff) < params.xg_diff_threshold:
        signals += 1
    if features.is_knockout:
        signals += 1
    return signals


def apply_draw_calibration(
    probs: Dict[str, float],
    features: DrawFeatures,
    params: DrawCalibrationParams = DEFAULT_DRAW_PARAMS,
) -> Dict[str, float]:
    """Boost draw only when independent draw signals agree."""
    base = normalize_probs(probs)
    if not params.enabled:
        return base

    signals = draw_signal_count(base, features, params)

    if signals < params.min_signals or base["draw"] < params.min_draw_prob:
        return base

    draw_new = min(params.draw_cap, base["draw"] * params.draw_boost)
    if params.promote_draw and signals >= params.promote_min_signals:
        draw_new = max(
            draw_new,
            min(params.draw_cap, max(base["home"], base["away"]) + params.promote_margin),
        )

    delta = max(0.0, draw_new - base["draw"])
    if delta <= 0:
        return base

    home_away_total = base["home"] + base["away"]
    if home_away_total <= 0:
        return normalize_probs({"home": 0.35, "draw": draw_new, "away": 0.35})

    calibrated = {
        "home": base["home"] - delta * (base["home"] / home_away_total),
        "draw": draw_new,
        "away": base["away"] - delta * (base["away"] / home_away_total),
    }
    return normalize_probs(calibrated)


def top_prediction(probs: Dict[str, float]) -> str:
    return max(probs, key=probs.get)


def brier_score(probs: Dict[str, float], actual: str) -> float:
    return sum((probs[k] - (1.0 if actual == k else 0.0)) ** 2 for k in OUTCOMES) / 3.0


def log_loss(probs: Dict[str, float], actual: str) -> float:
    return -math.log(max(probs.get(actual, 1e-6), 1e-6))


def evaluate_rows(rows: Iterable[Dict], params: Optional[DrawCalibrationParams] = None) -> Dict:
    total = correct = draw_predictions = actual_draws = 0
    brier = logloss = 0.0

    for row in rows:
        probs = row["probabilities"]
        if params is not None:
            probs = apply_draw_calibration(probs, row["draw_features"], params)
        else:
            probs = normalize_probs(probs)

        actual = row["actual_outcome"]
        pred = top_prediction(probs)
        total += 1
        correct += int(pred == actual)
        draw_predictions += int(pred == "draw")
        actual_draws += int(actual == "draw")
        brier += brier_score(probs, actual)
        logloss += log_loss(probs, actual)

    if total == 0:
        return {
            "total": 0,
            "correct": 0,
            "accuracy": 0.0,
            "brier": 0.0,
            "log_loss": 0.0,
            "draw_prediction_rate": 0.0,
            "actual_draw_rate": 0.0,
        }

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total,
        "brier": brier / total,
        "log_loss": logloss / total,
        "draw_prediction_rate": draw_predictions / total,
        "actual_draw_rate": actual_draws / total,
    }


def candidate_params() -> List[DrawCalibrationParams]:
    """Small grid intended for repeated walk-forward tuning."""
    candidates: List[DrawCalibrationParams] = [DrawCalibrationParams(enabled=False)]
    for min_signals in (2, 3):
        for min_draw_prob in (0.18, 0.20, 0.22):
            for draw_boost in (1.25, 1.40, 1.60, 1.85):
                for draw_cap in (0.42, 0.46):
                    for promote_draw in (False, True):
                        candidates.append(
                            DrawCalibrationParams(
                                enabled=True,
                                min_signals=min_signals,
                                min_draw_prob=min_draw_prob,
                                draw_boost=draw_boost,
                                draw_cap=draw_cap,
                                promote_draw=promote_draw,
                                promote_min_signals=3,
                                promote_margin=0.005,
                            )
                        )
    return candidates


def tune_params(rows: List[Dict], candidates: Optional[List[DrawCalibrationParams]] = None) -> Tuple[DrawCalibrationParams, Dict]:
    """Pick params on a training window.

    Accuracy is primary because the project currently tracks direction hit rate,
    but Brier/log-loss are kept as tie-breakers to avoid over-promoting draw.
    """
    if candidates is None:
        candidates = candidate_params()

    # Use the most recent slice of the training window as an inner validation
    # segment. This keeps the walk-forward process from picking a draw boost
    # that only worked on older seasons.
    selection_rows = rows[int(len(rows) * 0.75):] if len(rows) >= 400 else rows

    best_params = DEFAULT_DRAW_PARAMS
    best_metrics = evaluate_rows(selection_rows, best_params)
    best_score = _selection_score(best_metrics)

    for params in candidates:
        metrics = evaluate_rows(selection_rows, params)
        score = _selection_score(metrics)
        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics

    return best_params, best_metrics


def _selection_score(metrics: Dict) -> float:
    draw_penalty = max(0.0, metrics["draw_prediction_rate"] - 0.35) * 0.20
    return metrics["accuracy"] - draw_penalty - metrics["brier"] * 0.02
