"""P0: 特征 schema 与权重维度一致性断言。"""
from types import SimpleNamespace

import numpy as np
import pytest

from features.schema import (
    BASE_FEATURE_DIM,
    BASE_FEATURE_NAMES,
    FEATURE_DIM,
    FEATURE_NAMES,
    FULL_FEATURE_DIM,
    INTERACTION_DIM,
    assert_schema_integrity,
)
from features.feature_builder import FeatureBuilder
from fusion.logistic_fusion import FEATURE_NAMES as LR_FEATURE_NAMES


def test_schema_integrity():
    assert_schema_integrity()
    assert BASE_FEATURE_DIM == 48
    assert INTERACTION_DIM == 5
    assert FULL_FEATURE_DIM == 53
    assert FEATURE_DIM == 48
    assert len(FEATURE_NAMES) == 53
    assert len(BASE_FEATURE_NAMES) == 48


def test_lr_names_match_schema():
    assert LR_FEATURE_NAMES == FEATURE_NAMES


def test_builder_shapes():
    ctx = SimpleNamespace(
        match_id=1,
        home_team=SimpleNamespace(
            elo=1600, key_players_available=11, key_players_total=11, key_injuries="", rest_days=5
        ),
        away_team=SimpleNamespace(
            elo=1500, key_players_available=11, key_players_total=11, key_injuries="", rest_days=5
        ),
        is_knockout=False,
        overround=0.05,
        source_count=3,
    )
    elo = {"home": 0.5, "draw": 0.28, "away": 0.22}
    poisson = {
        "lambda_home": 1.5,
        "lambda_away": 1.0,
        "spf": {"home": 0.42, "draw": 0.28, "away": 0.30},
    }
    form = SimpleNamespace(to_list=lambda: [0.4, 0.25, 0.3, 0.6, 0.2])
    h2h = SimpleNamespace(to_list=lambda: [0.5, 0.6, 0.2, 0.5, 0.3, 0.0])

    base = FeatureBuilder(use_interactions=False).build(
        elo, poisson, 1.0, {"home": 0.4, "draw": 0.3, "away": 0.3}, form, h2h, ctx
    )
    full = FeatureBuilder(use_interactions=True).build(
        elo, poisson, 1.0, {"home": 0.4, "draw": 0.3, "away": 0.3}, form, h2h, ctx
    )
    assert base.shape == (48,)
    assert full.shape == (53,)
    assert np.isfinite(full).all()


def test_best_global_weight_dim_compatible():
    """最优 global 权重 (May 25) 必须是 48 维，与 base schema 对齐。"""
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parents[1] / "data" / "weights" / "lr" / "global_v1_2026-05-25.json"
    if not path.exists():
        pytest.skip("weight file not present")
    data = json.loads(path.read_text())
    assert len(data["coef_home"]) == 48
    assert float(data["accuracy"]) >= 0.55


def test_registry_active_global_not_degraded():
    from fusion.weights_registry import WeightsRegistry

    reg = WeightsRegistry()
    w = reg.get_active("global")
    assert w is not None
    assert float(w.accuracy) >= 0.52, f"active global acc too low: {w.accuracy}"
    assert len(w.coef_home) in (43, 48, 53, 55)
