"""Tests for FeatureBuilder (build/fit_scaler/transform)."""
from types import SimpleNamespace

import numpy as np
import pytest

from features.feature_builder import FeatureBuilder


def make_team(elo=1500, rest_days=7):
    return SimpleNamespace(
        elo=elo,
        name="Team",
        team_id=1,
        key_players_available=11,
        key_players_total=11,
        key_injuries="",
        rest_days=rest_days,
    )


def make_ctx(home_elo=1500, away_elo=1500, knockout=False):
    return SimpleNamespace(
        match_id=1,
        home_team=make_team(elo=home_elo),
        away_team=make_team(elo=away_elo),
        is_knockout=knockout,
        overround=0.05,
        source_count=3,
    )


def make_form(to_list=None):
    if to_list is None:
        to_list = lambda: [0.4, 0.25, 0.3, 0.6, 0.2]
    return SimpleNamespace(to_list=to_list)


def make_h2h(to_list=None):
    if to_list is None:
        to_list = lambda: [0.5, 0.6, 0.2, 0.5, 0.3, 0.0]
    return SimpleNamespace(to_list=to_list)


ELO = {"home": 0.5, "draw": 0.28, "away": 0.22}
POISSON = {
    "lambda_home": 1.5,
    "lambda_away": 1.0,
    "spf": {"home": 0.42, "draw": 0.28, "away": 0.30},
}


class TestBuild:
    """build() must return correct shape and value ranges."""

    def test_build_normal_shape(self):
        builder = FeatureBuilder(use_interactions=True)
        features = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), make_ctx())
        assert isinstance(features, np.ndarray)
        assert features.dtype == np.float32
        assert features.shape == (45,)

    def test_build_no_interactions_shape(self):
        builder = FeatureBuilder(use_interactions=False)
        features = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), make_ctx())
        assert features.shape == (40,)

    def test_build_missing_market_uses_defaults(self):
        builder = FeatureBuilder(use_interactions=False)
        features = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), make_ctx())
        assert np.allclose(features[18:21], [0.33, 0.33, 0.33], atol=1e-6)
        assert features[22] == 0.0  # max_odds_move
        assert features[23] == 0.0  # source_count

    def test_build_market_provided(self):
        builder = FeatureBuilder(use_interactions=False)
        market = {"home": 0.45, "draw": 0.30, "away": 0.25}
        features = builder.build(ELO, POISSON, 1.0, market, make_form(), make_h2h(), make_ctx())
        assert np.allclose(features[18:21], [0.45, 0.30, 0.25], atol=1e-6)

    def test_build_missing_form_uses_defaults(self):
        builder = FeatureBuilder(use_interactions=False)
        features = builder.build(ELO, POISSON, 1.0, None, None, make_h2h(), make_ctx())
        assert np.allclose(features[24:29], [0.33, 0.33, 0.0, 0.5, 0.0], atol=1e-6)

    def test_build_missing_h2h_uses_defaults(self):
        builder = FeatureBuilder(use_interactions=False)
        features = builder.build(ELO, POISSON, 1.0, None, make_form(), None, make_ctx())
        assert np.allclose(features[29:35], [0.0, 0.0, 0.0, 0.0, 0.0, 1.0], atol=1e-6)

    def test_build_knockout_affects_interaction(self):
        builder = FeatureBuilder(use_interactions=True)
        ctx_knock = make_ctx(home_elo=1600, away_elo=1400, knockout=True)
        ctx_group = make_ctx(home_elo=1600, away_elo=1400, knockout=False)
        knock = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), ctx_knock)
        group = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), ctx_group)
        assert knock[40] != group[40]  # elo_diff × is_knockout interaction (first interaction feature)

    def test_build_elo_diff_clipped(self):
        builder = FeatureBuilder(use_interactions=False)
        far = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), make_ctx(home_elo=2500, away_elo=500))
        assert -2.0 <= far[0] <= 2.0

    def test_build_all_features_in_range(self):
        builder = FeatureBuilder(use_interactions=True)
        features = builder.build(ELO, POISSON, 1.0, None, make_form(), make_h2h(), make_ctx())
        for i, val in enumerate(features):
            assert -3.0 <= val <= 3.0, f"Feature {i} out of range: {val}"


class TestScaler:
    """fit_scaler()/transform() must normalize correctly."""

    def test_fit_transform(self):
        builder = FeatureBuilder(use_interactions=True)
        X = np.random.randn(100, 43).astype(np.float32)
        builder.fit_scaler(X)
        transformed = builder.transform(X)
        assert transformed.shape == (100, 43)
        assert abs(np.mean(transformed)) < 0.5
        assert 0.5 < np.std(transformed) < 1.5

    def test_transform_without_fit_returns_identity(self):
        builder = FeatureBuilder(use_interactions=True)
        X = np.random.randn(10, 43).astype(np.float32)
        result = builder.transform(X)
        assert np.allclose(result, X)

    def test_get_input_dim(self):
        assert FeatureBuilder(use_interactions=True).get_input_dim() == 45
        assert FeatureBuilder(use_interactions=False).get_input_dim() == 40
