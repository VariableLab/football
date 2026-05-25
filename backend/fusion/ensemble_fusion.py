"""
Ensemble 融合器 — LR + RF + XGBoost 软投票

使用 sklearn LogisticRegression(多类) + RandomForestClassifier + XGBClassifier
做 soft voting（概率平均），替代单一多项式逻辑回归。
"""

import json
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from xgboost import XGBClassifier

from fusion.logistic_fusion import (
    LogisticFusionWeights, LogisticFusionTrainer, FEATURE_NAMES
)
from logger import get_logger

logger = get_logger("ensemble_fusion")

ENSEMBLE_DIR = "./data/weights/ensemble"
os.makedirs(ENSEMBLE_DIR, exist_ok=True)


@dataclass
class EnsembleWeights:
    lr_weights: LogisticFusionWeights
    rf_model: RandomForestClassifier
    xgb_model: XGBClassifier
    gbm_model: GradientBoostingClassifier = None

    league: str = "global"
    trained_at: str = ""
    sample_count: int = 0
    accuracy: float = 0.0
    brier: float = 0.0

    def predict(self, features: np.ndarray):
        if features.ndim == 1:
            features = features.reshape(1, -1)

        lr_probs = self.lr_weights.predict(features)
        if not isinstance(lr_probs, np.ndarray):
            lr_probs = np.array([[lr_probs["home"], lr_probs["draw"], lr_probs["away"]]])

        rf_probs = self.rf_model.predict_proba(features)
        rf_probs = np.column_stack([rf_probs[0], rf_probs[1], rf_probs[2]]) if isinstance(rf_probs, list) else rf_probs
        if len(rf_probs.shape) == 3:
            rf_probs = rf_probs[:, :, 1].T

        xgb_probs = self.xgb_model.predict_proba(features)

        gbm_probs = 0
        if self.gbm_model is not None:
            gbm_probs = self.gbm_model.predict_proba(features)

        n_models = 3 + (1 if self.gbm_model is not None else 0)
        avg_probs = (lr_probs + rf_probs + xgb_probs + gbm_probs) / n_models

        if avg_probs.shape[0] == 1:
            return {
                "home": float(avg_probs[0, 0]),
                "draw": float(avg_probs[0, 1]),
                "away": float(avg_probs[0, 2]),
            }
        return avg_probs

    def save(self) -> str:
        ts = self.trained_at[:10] if self.trained_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        base = f"{self.league}_ensemble_{ts}"
        lr_path = self.lr_weights.save()
        rf_path = os.path.join(ENSEMBLE_DIR, f"{base}_rf.pkl")
        xgb_path = os.path.join(ENSEMBLE_DIR, f"{base}_xgb.json")
        meta_path = os.path.join(ENSEMBLE_DIR, f"{base}_meta.json")

        with open(rf_path, "wb") as f:
            pickle.dump(self.rf_model, f)
        self.xgb_model.save_model(xgb_path)

        meta = {
            "league": self.league,
            "trained_at": self.trained_at,
            "sample_count": self.sample_count,
            "accuracy": self.accuracy,
            "brier": self.brier,
            "lr_file": os.path.basename(lr_path),
            "rf_file": os.path.basename(rf_path),
            "xgb_file": os.path.basename(xgb_path),
            "has_gbm": self.gbm_model is not None,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.info(f"[ensemble] Saved to {base}")
        return base

    @classmethod
    def load(cls, base_name: str) -> "EnsembleWeights":
        base = os.path.join(ENSEMBLE_DIR, base_name)
        with open(f"{base}_meta.json") as f:
            meta = json.load(f)

        lr_path = os.path.join(LogisticFusionWeights.WEIGHTS_DIR, meta["lr_file"])
        with open(f"{base}_rf.pkl", "rb") as f:
            rf = pickle.load(f)
        xgb = XGBClassifier()
        xgb.load_model(f"{base}_xgb.json")

        return cls(
            lr_weights=LogisticFusionWeights.load(lr_path),
            rf_model=rf,
            xgb_model=xgb,
            **{k: meta[k] for k in ["league", "trained_at", "sample_count", "accuracy", "brier"]}
        )


class EnsembleTrainer:
    def __init__(
        self,
        l1_penalty: float = 0.001,
        rf_n_estimators: int = 300,
        rf_max_depth: int = 10,
        xgb_n_estimators: int = 300,
        xgb_max_depth: int = 6,
        xgb_learning_rate: float = 0.05,
        use_gbm: bool = False,
        class_weight: Optional[Dict[int, float]] = None,
        random_state: int = 42,
    ):
        self.l1_penalty = l1_penalty
        self.rf_n_estimators = rf_n_estimators
        self.rf_max_depth = rf_max_depth
        self.xgb_n_estimators = xgb_n_estimators
        self.xgb_max_depth = xgb_max_depth
        self.xgb_learning_rate = xgb_learning_rate
        self.use_gbm = use_gbm
        self.class_weight = class_weight
        self.random_state = random_state

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        league: str = "global",
    ) -> EnsembleWeights:
        N, D = X.shape
        logger.info(f"[ensemble] Training on {N} samples, {D} features")

        # 1. Train LR
        logger.info("[ensemble] Training LogisticRegression...")
        lr = LogisticFusionTrainer(
            l1_penalty=self.l1_penalty, max_iter=2000, class_weight=self.class_weight
        )
        lr_weights = lr.fit(X, y, league=league)

        # 2. Train RF
        logger.info("[ensemble] Training RandomForest...")
        sample_weight = None
        if self.class_weight:
            sample_weight = np.array([self.class_weight.get(int(yi), 1.0) for yi in y])
        rf = RandomForestClassifier(
            n_estimators=self.rf_n_estimators,
            max_depth=self.rf_max_depth,
            min_samples_leaf=5,
            class_weight="balanced" if not self.class_weight else None,
            random_state=self.random_state,
            n_jobs=-1,
        )
        rf.fit(X, y, sample_weight=sample_weight)

        # 3. Train XGBoost
        logger.info("[ensemble] Training XGBoost...")
        xgb_scale = [1.0, 1.0, 1.0]
        if self.class_weight:
            xgb_scale = [self.class_weight.get(i, 1.0) for i in range(3)]
        xgb = XGBClassifier(
            n_estimators=self.xgb_n_estimators,
            max_depth=self.xgb_max_depth,
            learning_rate=self.xgb_learning_rate,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            reg_alpha=0.1,
            random_state=self.random_state,
            n_jobs=-1,
        )
        xgb.fit(X, y, sample_weight=np.array([xgb_scale[int(yi)] for yi in y]))

        # 4. Optional GBM
        gbm = None
        if self.use_gbm:
            logger.info("[ensemble] Training GradientBoosting...")
            gbm = GradientBoostingClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                random_state=self.random_state,
            )
            gbm.fit(X, y, sample_weight=sample_weight)

        # Evaluate
        ensemble = EnsembleWeights(
            lr_weights=lr_weights,
            rf_model=rf,
            xgb_model=xgb,
            gbm_model=gbm,
            league=league,
            trained_at=datetime.now(timezone.utc).isoformat(),
            sample_count=N,
        )

        probs = ensemble.predict(X)
        if isinstance(probs, np.ndarray):
            preds = np.argmax(probs, axis=1)
            acc = float(np.mean(preds == y))
            ensemble.accuracy = acc
            logger.info(f"[ensemble] Train accuracy: {acc:.4f}")

        return ensemble
