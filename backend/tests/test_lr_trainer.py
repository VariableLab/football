"""Tests for LogisticFusionTrainer with synthetic data."""
import numpy as np

from fusion.logistic_fusion import LogisticFusionTrainer, LogisticFusionWeights


class TestTrainer:
    """fit() must converge and return LogisticFusionWeights."""

    def test_fit_converges_simple(self):
        np.random.seed(42)
        n = 200
        X = np.zeros((n, 43), dtype=np.float64)
        y = np.zeros(n, dtype=np.int32)
        X[:100, 0] = np.random.randn(100) * 0.5 + 1.0
        X[100:, 0] = np.random.randn(100) * 0.5 - 1.0
        y[:100] = 0
        y[100:] = 2
        trainer = LogisticFusionTrainer(l1_penalty=0.0, max_iter=500, verbose=False)
        weights = trainer.fit(X, y)
        assert isinstance(weights, LogisticFusionWeights)
        assert weights.accuracy > 0.5
        assert weights.sample_count == 200
        assert weights.coef_home[0] > weights.coef_away[0]

    def test_fit_three_class_distinguishable(self):
        np.random.seed(42)
        n = 300
        X = np.zeros((n, 43), dtype=np.float64)
        y = np.zeros(n, dtype=np.int32)
        for i in range(3):
            X[i * 100:(i + 1) * 100, 0] = np.random.randn(100) + [1.0, 0.0, -1.0][i]
            y[i * 100:(i + 1) * 100] = i
        trainer = LogisticFusionTrainer(l1_penalty=0.0, max_iter=500, verbose=False)
        weights = trainer.fit(X, y)
        assert weights.accuracy > 0.33

    def test_fit_returns_valid_weights_object(self):
        np.random.seed(42)
        X = np.random.randn(100, 43).astype(np.float64)
        y = np.random.randint(0, 3, size=100)
        trainer = LogisticFusionTrainer(l1_penalty=0.001, max_iter=100, verbose=False)
        weights = trainer.fit(X, y)
        assert isinstance(weights, LogisticFusionWeights)
        assert weights.coef_home.shape == (43,)
        assert weights.coef_away.shape == (43,)
        assert weights.sample_count == 100
        assert 0.0 <= weights.accuracy <= 1.0

    def test_fit_class_weight_affects_prediction(self):
        np.random.seed(42)
        n = 300
        X = np.random.randn(n, 43).astype(np.float64)
        y = np.random.randint(0, 3, size=n)
        y[:200] = 1
        trainer_no = LogisticFusionTrainer(l1_penalty=0.0, max_iter=300, verbose=False)
        trainer_cw = LogisticFusionTrainer(
            l1_penalty=0.0, max_iter=300, verbose=False,
            class_weight={1: 5.0},
        )
        w_no = trainer_no.fit(X, y)
        w_cw = trainer_cw.fit(X, y)
        X_test = np.random.randn(10, 43).astype(np.float64)
        preds_no = np.array([w_no.predict(x)["draw"] for x in X_test])
        preds_cw = np.array([w_cw.predict(x)["draw"] for x in X_test])
        assert np.mean(preds_cw) > np.mean(preds_no)

    def test_fit_l1_produces_sparse_coef(self):
        np.random.seed(42)
        X = np.random.randn(200, 43).astype(np.float64)
        y = np.random.randint(0, 3, size=200)
        trainer = LogisticFusionTrainer(l1_penalty=1.0, max_iter=500, verbose=False)
        weights = trainer.fit(X, y)
        nz_home = np.sum(np.abs(weights.coef_home) > 1e-4)
        assert nz_home < 43

    def test_fit_with_zero_l1_penalty(self):
        np.random.seed(42)
        X = np.random.randn(50, 43).astype(np.float64)
        y = np.random.randint(0, 3, size=50)
        trainer = LogisticFusionTrainer(l1_penalty=0.0, max_iter=100, verbose=False)
        weights = trainer.fit(X, y)
        assert weights.accuracy >= 0.0
        assert weights.sample_count == 50
