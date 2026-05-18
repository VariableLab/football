"""Tests for LogisticFusionWeights (predict/save/load/explain)."""
import json
import os

import numpy as np
import pytest

from fusion.logistic_fusion import LogisticFusionWeights


class TestPredict:
    """predict() must return valid probability distributions."""

    def test_predict_1d_returns_dict(self):
        w = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
        )
        features = np.zeros(43, dtype=np.float32)
        result = w.predict(features)
        assert isinstance(result, dict)
        assert set(result.keys()) == {"home", "draw", "away"}
        for v in result.values():
            assert 0.0 <= v <= 1.0
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_predict_sum_to_one(self):
        w = LogisticFusionWeights(
            coef_home=np.random.randn(43).astype(np.float64),
            coef_away=np.random.randn(43).astype(np.float64),
        )
        for _ in range(10):
            features = np.random.randn(43).astype(np.float32)
            result = w.predict(features)
            assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_predict_2d_returns_array(self):
        w = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
        )
        features = np.zeros((5, 43), dtype=np.float32)
        result = w.predict(features)
        assert isinstance(result, np.ndarray)
        assert result.shape == (5, 3)
        assert np.allclose(result.sum(axis=1), 1.0)

    def test_predict_positive_coef_increases_home_prob(self):
        w = LogisticFusionWeights(
            coef_home=np.array([10.0] + [0.0] * 42, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
        )
        features = np.zeros(43, dtype=np.float32)
        features[0] = 1.0
        result = w.predict(features)
        assert result["home"] > 0.5

    def test_predict_symmetric_flip(self):
        w = LogisticFusionWeights(
            coef_home=np.array([1.0] + [0.0] * 42, dtype=np.float64),
            coef_away=np.array([-1.0] + [0.0] * 42, dtype=np.float64),
        )
        pos = w.predict(np.array([1.0] + [0.0] * 42, dtype=np.float32))
        neg = w.predict(np.array([-1.0] + [0.0] * 42, dtype=np.float32))
        assert abs(pos["home"] - neg["away"]) < 1e-6
        assert abs(pos["away"] - neg["home"]) < 1e-6

    def test_predict_draw_high_when_all_logodds_zero(self):
        w = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
        )
        result = w.predict(np.zeros(43, dtype=np.float32))
        assert abs(result["draw"] - 1 / 3) < 1e-6

    def test_predict_uses_intercept(self):
        w = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
            intercept_home=2.0,
            intercept_away=-2.0,
        )
        result = w.predict(np.zeros(43, dtype=np.float32))
        assert result["home"] > result["draw"]
        assert result["away"] < result["draw"]


class TestSaveLoad:
    """save() then load() must round-trip identically."""

    def test_round_trip(self, tmp_path):
        w = LogisticFusionWeights(
            coef_home=np.array([0.5, -0.3] + [0.0] * 41, dtype=np.float64),
            coef_away=np.array([-0.2, 0.7] + [0.0] * 41, dtype=np.float64),
            intercept_home=0.1,
            intercept_away=-0.1,
            l1_penalty=0.01,
            input_dim=43,
            league="test",
            trained_at="2026-05-16T00:00:00",
            sample_count=100,
            cross_entropy=0.5,
            accuracy=0.55,
        )
        path = os.path.join(str(tmp_path), "test_weights.json")
        saved_path = w.save(path)
        assert saved_path == path
        assert os.path.exists(path)

        loaded = LogisticFusionWeights.load(path)
        assert np.allclose(loaded.coef_home, w.coef_home)
        assert np.allclose(loaded.coef_away, w.coef_away)
        assert loaded.intercept_home == 0.1
        assert loaded.intercept_away == -0.1
        assert loaded.l1_penalty == 0.01
        assert loaded.sample_count == 100
        assert loaded.accuracy == 0.55
        assert loaded.league == "test"

    def test_load_with_minimal_params(self, tmp_path):
        data = {
            "coef_home": [0.1] * 43,
            "coef_away": [-0.1] * 43,
        }
        path = os.path.join(str(tmp_path), "minimal.json")
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = LogisticFusionWeights.load(path)
        assert loaded.intercept_home == 0.0
        assert loaded.intercept_away == 0.0
        assert loaded.sample_count == 0
        assert loaded.accuracy == 0.0
        assert loaded.input_dim == 43
        assert loaded.league == "global"

    def test_save_auto_filename(self):
        w = LogisticFusionWeights(
            coef_home=np.zeros(43, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
            league="global",
            trained_at="2026-05-16T12:00:00",
        )
        name = w.save()
        assert "global_v1_2026-05-16.json" in name

    def test_load_nonexistent_file_raises(self):
        with pytest.raises((FileNotFoundError, json.JSONDecodeError)):
            LogisticFusionWeights.load("/nonexistent/path.json")


class TestExplain:
    """explain() must return correct structure."""

    def test_explain_structure(self):
        w = LogisticFusionWeights(
            coef_home=np.full(43, 0.1, dtype=np.float64),
            coef_away=np.full(43, -0.1, dtype=np.float64),
        )
        features = np.arange(43, dtype=np.float32) / 43.0
        explanations = w.explain(features)
        assert len(explanations) == 43
        for item in explanations:
            assert set(item.keys()) == {
                "feature", "value", "coef_home", "coef_away",
                "contrib_home", "contrib_away",
            }
            assert isinstance(item["feature"], str)
            assert isinstance(item["value"], float)
            assert isinstance(item["contrib_home"], float)

    def test_explain_matches_predict_direction(self):
        w = LogisticFusionWeights(
            coef_home=np.array([5.0] + [0.0] * 42, dtype=np.float64),
            coef_away=np.zeros(43, dtype=np.float64),
        )
        features = np.array([1.0] + [0.0] * 42, dtype=np.float32)
        explanations = w.explain(features)
        home_contribs = [e["contrib_home"] for e in explanations]
        assert max(home_contribs) > 0
        elo_entry = explanations[0]
        assert elo_entry["feature"] == "elo_diff"
        assert elo_entry["contrib_home"] > elo_entry["contrib_away"]
