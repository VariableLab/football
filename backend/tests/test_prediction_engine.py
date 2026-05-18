"""Tests for PredictionEngine with mocked sub-models."""
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from prediction_engine import PredictionEngine


def make_mock_team(name="TeamA", elo=1500):
    t = MagicMock()
    t.team_id = 1
    t.name = name
    t.elo = elo
    t.avg_goals_scored = 1.3
    t.avg_goals_conceded = 1.1
    t.avg_xg = 0.0
    t.avg_xga = 0.0
    t.possession = 50.0
    t.pass_completion = 0.0
    t.shots_per_game = 0.0
    t.form_factor = 1.0
    t.fifa_rank = 50
    t.key_players_available = 11
    t.key_players_total = 11
    t.key_injuries = ""
    t.squad_fatigue_index = 0.5
    t.tournament_matches_played = 0
    t.tournament_goals_scored = 0
    t.tournament_goals_conceded = 0
    t.recent_results = ""
    t.recent_goals_scored = 0.0
    t.recent_goals_conceded = 0.0
    t.home_away_factor = 1.0
    t.weather_adaptability = 1.0
    t.tactical_style = "balanced"
    t.coach_rating = 0.5
    t.rest_days = 7
    return t


def make_mock_ctx():
    ctx = MagicMock()
    ctx.match_id = 1
    ctx.home_team = make_mock_team("TeamA", elo=1600)
    ctx.away_team = make_mock_team("TeamB", elo=1400)
    ctx.stage = "group"
    ctx.is_knockout = False
    ctx.handicap = 0
    ctx.odds_home = 2.0
    ctx.odds_draw = 3.2
    ctx.odds_away = 3.5
    ctx.overround = 0.05
    ctx.source_count = 3
    ctx.has_closing_odds = False
    ctx.has_odds = True
    ctx.venue_type = "neutral"
    return ctx


@pytest.fixture
def mock_submodels():
    patches = [
        patch("prediction_engine.EloModel.predict", return_value={"home": 0.5, "draw": 0.28, "away": 0.22}),
        patch("prediction_engine.PoissonModel.predict", return_value={
            "lambda_home": 1.5, "lambda_away": 1.0,
            "spf": {"home": 0.42, "draw": 0.28, "away": 0.30},
            "rq": {"home": 0.38, "draw": 0.28, "away": 0.34},
            "score": {"0-0": 0.1}, "goals": {"0": 0.2}, "half": {"hh": 0.15},
        }),
        patch("prediction_engine.PlayerAdjustmentModel.predict", return_value=1.0),
        patch("prediction_engine.MarketModel.predict", return_value={"home": 0.45, "draw": 0.30, "away": 0.25}),
        patch("prediction_engine.DrawDetectionModel.predict", side_effect=lambda spf, ctx, m: spf),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


class TestPredictionEngine:
    """Predict() must return correct structure on both LR and fallback paths."""

    def test_predict_lr_path_structure(self, mock_submodels):
        from fusion.logistic_fusion import LogisticFusionWeights
        mock_weights = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
            league="global", trained_at="2026-05-16",
            accuracy=0.55, sample_count=100,
        )
        with patch.object(PredictionEngine, "_load_lr_weights", return_value=mock_weights):
            engine = PredictionEngine(use_lr_fusion=True)
        result = engine.predict(make_mock_ctx())
        assert result.model_version == "v2.0-lr"
        assert set(result.spf.keys()) == {"home", "draw", "away"}
        assert abs(sum(result.spf.values()) - 1.0) < 1e-6
        assert result.confidence in ("high", "medium", "low")

    def test_predict_fallback_structure(self, mock_submodels):
        engine = PredictionEngine(use_lr_fusion=False)
        result = engine.predict(make_mock_ctx())
        assert result.model_version == "v1.0"
        assert set(result.spf.keys()) == {"home", "draw", "away"}
        assert abs(sum(result.spf.values()) - 1.0) < 1e-6

    def test_predict_all_output_keys_present(self, mock_submodels):
        engine = PredictionEngine(use_lr_fusion=False)
        result = engine.predict(make_mock_ctx())
        for attr in ("spf", "rq", "score", "goals", "half"):
            assert hasattr(result, attr)
        for d in [result.spf, result.rq]:
            assert sum(d.values()) > 0.99

    def test_predict_odds_degraded_flag(self, mock_submodels):
        ctx = make_mock_ctx()
        ctx.odds_home = None
        ctx.odds_draw = None
        ctx.odds_away = None
        with patch("prediction_engine.MarketModel.predict", return_value=None):
            engine = PredictionEngine(use_lr_fusion=False)
            result = engine.predict(ctx)
        assert result.odds_degraded

    def test_predict_no_extreme_confidence_on_balanced(self, mock_submodels):
        engine = PredictionEngine(use_lr_fusion=False)
        result = engine.predict(make_mock_ctx())
        assert max(result.spf.values()) < 0.95
