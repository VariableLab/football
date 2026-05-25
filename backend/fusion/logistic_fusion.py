"""
多项式逻辑回归融合器 — LogisticFusion

替代旧的 4 参数线性加权 (EnsembleFusion)，使用 L-BFGS-B 优化
多项式逻辑回归的 cross-entropy loss + L1 正则化。

数学:
  log(P_home / P_draw) = beta_home · X
  log(P_away / P_draw) = beta_away · X
  P = softmax([logodds_h, 0, logodds_a])

特性:
  - 自然输出校准概率
  - L1 正则化自动特征选择
  - 系数即特征贡献，完全可解释
  - 支持联赛分层训练
  - 兼容 scipy.optimize.minimize (已有依赖)
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax

from logger import get_logger

logger = get_logger("logistic_fusion")

# 权重存储目录
WEIGHTS_DIR = "./data/weights/lr"
os.makedirs(WEIGHTS_DIR, exist_ok=True)

# 特征名称（与 feature_builder 对齐）
FEATURE_NAMES = [
    "elo_diff", "elo_win", "elo_draw", "elo_away",
    "is_heavy_fav", "is_heavy_udog", "elo_tier_diff",
    "lambda_home", "lambda_away", "lambda_diff",
    "poisson_win", "poisson_draw", "poisson_away", "goal_exp",
    "home_avail", "away_avail", "avail_diff", "injury_impact",
    "market_win", "market_draw", "market_away",
    "overround", "max_odds_move", "source_count",
    "form_win", "form_draw", "momentum", "stability", "streak_norm",
    "h2h_total_norm", "h2h_win", "h2h_draw", "h2h_recent", "h2h_goals_norm", "first_meeting",
    "rest_advantage", "is_knockout", "is_derby",
    "ref_severity", "ref_home_bias",
    "home_rest", "away_rest", "is_late_season",
    # 交互特征
    "I_elo_knockout", "I_model_disagree", "I_momentum_rest",
    "I_market_source", "I_elo_form",
]


@dataclass
class LogisticFusionWeights:
    """逻辑回归融合权重"""
    coef_home: np.ndarray          # (D,) 主胜 log-odds 系数
    coef_away: np.ndarray          # (D,) 客胜 log-odds 系数
    intercept_home: float = 0.0
    intercept_away: float = 0.0
    l1_penalty: float = 0.001
    input_dim: int = 48

    # 元信息
    league: str = "global"
    trained_at: str = ""
    sample_count: int = 0
    cross_entropy: float = 0.0
    accuracy: float = 0.0

    def predict(self, features: np.ndarray) -> Dict[str, float]:
        """
        推理：特征 → 概率。

        Args:
            features: shape (D,) 或 (N, D)

        Returns:
            {"home": float, "draw": float, "away": float}
            或批量时返回三个等长数组
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
            
        # 维度检查
        if features.shape[1] != self.coef_home.shape[0]:
            raise ValueError(
                f"Feature dimension mismatch: expected {self.coef_home.shape[0]}, "
                f"got {features.shape[1]}. Model may need retraining."
            )

        logodds_home = features @ self.coef_home + self.intercept_home
        logodds_away = features @ self.coef_away + self.intercept_away

        # 构造 (N, 3) log-odds，draw 始终为 0
        logodds = np.column_stack([
            logodds_home,
            np.zeros_like(logodds_home),
            logodds_away,
        ])

        probs = softmax(logodds, axis=1)

        if probs.shape[0] == 1:
            return {
                "home": float(probs[0, 0]),
                "draw": float(probs[0, 1]),
                "away": float(probs[0, 2]),
            }
        return probs

    def explain(self, features: np.ndarray) -> List[Dict]:
        """返回每个特征对 home/away 概率的贡献"""
        if features.ndim != 1:
            features = features.flatten()

        explanations = []
        for i, name in enumerate(FEATURE_NAMES[:len(self.coef_home)]):
            explanations.append({
                "feature": name,
                "value": round(float(features[i]), 4),
                "coef_home": round(float(self.coef_home[i]), 4),
                "coef_away": round(float(self.coef_away[i]), 4),
                "contrib_home": round(float(self.coef_home[i] * features[i]), 4),
                "contrib_away": round(float(self.coef_away[i] * features[i]), 4),
            })
        return explanations

    def save(self, filename: Optional[str] = None) -> str:
        """保存权重到 JSON"""
        if filename is None:
            filename = f"{self.league}_v1_{self.trained_at[:10]}.json"
        path = os.path.join(WEIGHTS_DIR, filename)
        data = {
            "coef_home": self.coef_home.tolist(),
            "coef_away": self.coef_away.tolist(),
            "intercept_home": self.intercept_home,
            "intercept_away": self.intercept_away,
            "l1_penalty": self.l1_penalty,
            "input_dim": self.input_dim,
            "league": self.league,
            "trained_at": self.trained_at,
            "sample_count": self.sample_count,
            "cross_entropy": self.cross_entropy,
            "accuracy": self.accuracy,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[logistic_fusion] Weights saved to {path}")
        return path

    @classmethod
    def load(cls, filepath: str) -> "LogisticFusionWeights":
        """从 JSON 加载权重"""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(
            coef_home=np.array(data["coef_home"], dtype=np.float64),
            coef_away=np.array(data["coef_away"], dtype=np.float64),
            intercept_home=data.get("intercept_home", 0.0),
            intercept_away=data.get("intercept_away", 0.0),
            l1_penalty=data.get("l1_penalty", 0.001),
            input_dim=data.get("input_dim", 48),
            league=data.get("league", "global"),
            trained_at=data.get("trained_at", ""),
            sample_count=data.get("sample_count", 0),
            cross_entropy=data.get("cross_entropy", 0.0),
            accuracy=data.get("accuracy", 0.0),
        )


class LogisticFusionTrainer:
    """
    逻辑回归融合训练器。

    优化: L-BFGS-B 最小化 cross-entropy + L1 penalty
    """

    def __init__(
        self,
        l1_penalty: float = 0.001,
        max_iter: int = 1000,
        verbose: bool = False,
        class_weight: Optional[Dict[int, float]] = None,
    ):
        self.l1_penalty = l1_penalty
        self.max_iter = max_iter
        self.verbose = verbose
        self.class_weight = class_weight

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        league: str = "global",
    ) -> LogisticFusionWeights:
        """
        训练逻辑回归融合器。

        Args:
            X: shape (N, D) 特征矩阵
            y: shape (N,) 标签，0=home, 1=draw, 2=away
            league: 联赛标签（用于保存时标记）

        Returns:
            LogisticFusionWeights
        """
        N, D = X.shape
        logger.info(f"[logistic_fusion] Training on {N} samples, {D} features, l1={self.l1_penalty}")

        # 初始化参数: [beta_home (D), beta_away (D), intercept_home, intercept_away]
        init_params = np.zeros(2 * D + 2)

        # L-BFGS-B 优化
        result = minimize(
            fun=self._loss_and_grad,
            x0=init_params,
            args=(X, y),
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": self.max_iter, "disp": self.verbose},
        )

        # 解析参数
        beta_home = result.x[:D]
        beta_away = result.x[D:2*D]
        intercept_home = result.x[2*D]
        intercept_away = result.x[2*D + 1]

        # 评估
        weights = LogisticFusionWeights(
            coef_home=beta_home,
            coef_away=beta_away,
            intercept_home=intercept_home,
            intercept_away=intercept_away,
            l1_penalty=self.l1_penalty,
            input_dim=D,
            league=league,
            trained_at=datetime.now(timezone.utc).isoformat(),
            sample_count=N,
            cross_entropy=float(result.fun),
        )

        # 计算准确率
        probs = weights.predict(X)
        if isinstance(probs, np.ndarray):
            preds = np.argmax(probs, axis=1)
            weights.accuracy = float(np.mean(preds == y))

        # 打印特征重要性
        self._log_feature_importance(weights)

        return weights

    def _loss_and_grad(
        self,
        params: np.ndarray,
        X: np.ndarray,
        y: np.ndarray,
    ) -> Tuple[float, np.ndarray]:
        """
        计算 cross-entropy loss + L1 penalty 及其梯度。

        params = [beta_home (D), beta_away (D), intercept_home, intercept_away]
        """
        N, D = X.shape
        beta_home = params[:D]
        beta_away = params[D:2*D]
        intercept_home = params[2*D]
        intercept_away = params[2*D + 1]

        # log-odds
        logodds_home = X @ beta_home + intercept_home
        logodds_away = X @ beta_away + intercept_away
        logodds_draw = np.zeros(N)

        # softmax
        logodds = np.column_stack([logodds_home, logodds_draw, logodds_away])
        logodds_max = np.max(logodds, axis=1, keepdims=True)
        exp_shifted = np.exp(logodds - logodds_max)
        probs = exp_shifted / np.sum(exp_shifted, axis=1, keepdims=True)

        # cross-entropy loss (with optional class_weight)
        y_onehot = np.zeros((N, 3))
        y_onehot[np.arange(N), y] = 1
        eps = 1e-15
        if self.class_weight:
            sample_weights = np.array([self.class_weight.get(int(yi), 1.0) for yi in y], dtype=np.float64)
            ce_per_sample = -np.sum(y_onehot * np.log(probs + eps), axis=1)
            ce_loss = np.mean(sample_weights * ce_per_sample)
        else:
            ce_loss = -np.mean(np.sum(y_onehot * np.log(probs + eps), axis=1))

        # L1 penalty
        l1_pen = self.l1_penalty * (np.sum(np.abs(beta_home)) + np.sum(np.abs(beta_away)))

        loss = ce_loss + l1_pen

        # ─── 梯度 ───
        # dL/d(logodds) = probs - y_onehot (weighted if class_weight set)
        if self.class_weight:
            sample_weights = np.array([self.class_weight.get(int(yi), 1.0) for yi in y], dtype=np.float64)
            grad_logodds = (probs - y_onehot) * sample_weights[:, np.newaxis] / N
        else:
            grad_logodds = (probs - y_onehot) / N  # (N, 3)

        # beta_home 梯度 (第0列) + L1 subgradient
        grad_beta_home = X.T @ grad_logodds[:, 0] + self.l1_penalty * np.sign(beta_home)
        # beta_away 梯度 (第2列) + L1 subgradient
        grad_beta_away = X.T @ grad_logodds[:, 2] + self.l1_penalty * np.sign(beta_away)
        # intercept 梯度
        grad_intercept_home = np.sum(grad_logodds[:, 0])
        grad_intercept_away = np.sum(grad_logodds[:, 2])

        grad = np.concatenate([grad_beta_home, grad_beta_away,
                               [grad_intercept_home, grad_intercept_away]])

        return loss, grad

    @staticmethod
    def _log_feature_importance(weights: LogisticFusionWeights) -> None:
        """打印 Top 10 最重要特征"""
        importance_home = np.abs(weights.coef_home)
        importance_away = np.abs(weights.coef_away)
        importance = importance_home + importance_away

        top_idx = np.argsort(-importance)[:10]
        lines = []
        for i in top_idx:
            name = FEATURE_NAMES[i] if i < len(FEATURE_NAMES) else f"feat_{i}"
            lines.append(
                f"  {name:25s}  home={weights.coef_home[i]:+.4f}  away={weights.coef_away[i]:+.4f}"
            )
        logger.info(f"[logistic_fusion] Top 10 features:\n" + "\n".join(lines))


def cross_validate_lambda(
    X: np.ndarray,
    y: np.ndarray,
    lambdas: List[float] = None,
    n_folds: int = 5,
    class_weight: Optional[Dict[int, float]] = None,
) -> Tuple[float, Dict[float, float]]:
    """
    交叉验证选择最优 L1 正则化强度。

    Returns:
        (best_lambda, {lambda: avg_accuracy})
    """
    if lambdas is None:
        lambdas = [0.0, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05]

    results = {}
    best_lambda = lambdas[0]
    best_acc = 0.0

    N = X.shape[0]
    fold_size = N // n_folds

    for lam in lambdas:
        accs = []
        for fold in range(n_folds):
            val_start = fold * fold_size
            val_end = val_start + fold_size if fold < n_folds - 1 else N

            val_idx = np.arange(val_start, val_end)
            train_idx = np.concatenate([
                np.arange(0, val_start),
                np.arange(val_end, N),
            ])

            X_train, y_train = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx], y[val_idx]

            trainer = LogisticFusionTrainer(l1_penalty=lam, max_iter=500, class_weight=class_weight)
            weights = trainer.fit(X_train, y_train)

            probs = weights.predict(X_val)
            if isinstance(probs, np.ndarray):
                preds = np.argmax(probs, axis=1)
                accs.append(float(np.mean(preds == y_val)))

        avg_acc = np.mean(accs) if accs else 0.0
        results[lam] = avg_acc
        if avg_acc > best_acc:
            best_acc = avg_acc
            best_lambda = lam

    return best_lambda, results
