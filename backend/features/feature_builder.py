"""
特征拼接器 — FeatureBuilder

将 Layer 1 子模型输出拼接为权威特征向量，供 LR 融合使用。

维度 (单一真相源: features.schema):
  基线 48 + 交互 5 = 53 (use_interactions=True)
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from features.schema import (
    BASE_FEATURE_DIM,
    BASE_FEATURE_NAMES,
    FEATURE_DIM,
    FEATURE_NAMES,
    FULL_FEATURE_DIM,
    INTERACTION_DIM,
)
from utils.logger import get_logger

logger = get_logger("feature_builder")

# 再导出，保持 `from features.feature_builder import FEATURE_DIM` 兼容
__all__ = [
    "FeatureBuilder",
    "FEATURE_DIM",
    "FEATURE_NAMES",
    "BASE_FEATURE_DIM",
    "FULL_FEATURE_DIM",
    "INTERACTION_DIM",
]


class FeatureBuilder:
    """高精度特征拼接器 — 与 schema 严格对齐。"""

    def __init__(self, use_interactions: bool = True):
        self.use_interactions = use_interactions
        self._feature_mean: Optional[np.ndarray] = None
        self._feature_std: Optional[np.ndarray] = None

    def build(
        self,
        elo_probs: Dict[str, float],
        poisson_result: Dict,
        players_factor: float,
        market_probs: Optional[Dict[str, float]],
        form_features,
        h2h_features,
        ctx,
    ) -> np.ndarray:
        """
        拼接特征向量。
        - use_interactions=False → shape (48,)
        - use_interactions=True  → shape (53,)
        """
        feats = []

        # ─── A. Elo (8) ───
        elo_home = getattr(ctx.home_team, "elo", 1500)
        elo_away = getattr(ctx.away_team, "elo", 1500)
        elo_diff = (elo_home - elo_away) / 400.0
        elo_drift = getattr(ctx, "elo_drift", 0.0)

        feats.extend([
            np.clip(elo_diff, -2.0, 2.0),
            elo_probs.get("home", 0.33),
            elo_probs.get("draw", 0.33),
            elo_probs.get("away", 0.33),
            1.0 if elo_diff > 0.5 else 0.0,
            1.0 if elo_diff < -0.5 else 0.0,
            np.clip((elo_home - 1400) / 600.0, -1.0, 1.0),
            np.clip(elo_drift / 50.0, -1.0, 1.0),
        ])

        # ─── B. Poisson (8) ───
        lam_h = poisson_result.get("lambda_home", 1.2)
        lam_a = poisson_result.get("lambda_away", 1.0)
        poisson_spf = poisson_result.get("spf", {})
        rel_goals = (lam_h / max(lam_a, 0.1)) / 5.0

        feats.extend([
            np.clip(lam_h / 3.0, 0.0, 1.0),
            np.clip(lam_a / 3.0, 0.0, 1.0),
            np.clip((lam_h - lam_a) / 2.0, -1.0, 1.0),
            poisson_spf.get("home", 0.33),
            poisson_spf.get("draw", 0.33),
            poisson_spf.get("away", 0.33),
            np.clip((lam_h + lam_a) / 5.0, 0.0, 1.0),
            np.clip(rel_goals, -1.0, 2.0),
        ])

        # ─── C. Players (4) ───
        h_avail = getattr(ctx.home_team, "key_players_available", 11) / max(
            getattr(ctx.home_team, "key_players_total", 11), 1
        )
        a_avail = getattr(ctx.away_team, "key_players_available", 11) / max(
            getattr(ctx.away_team, "key_players_total", 11), 1
        )
        h_inj = (
            len((getattr(ctx.home_team, "key_injuries", "") or "").split(","))
            if getattr(ctx.home_team, "key_injuries", "")
            else 0
        )
        a_inj = (
            len((getattr(ctx.away_team, "key_injuries", "") or "").split(","))
            if getattr(ctx.away_team, "key_injuries", "")
            else 0
        )
        feats.extend([
            h_avail,
            a_avail,
            h_avail - a_avail,
            min((h_inj + a_inj) / 10.0, 1.0),
        ])

        # ─── D. Market (7) ───
        if market_probs:
            m_move = getattr(ctx, "max_odds_move", 0.0)
            vol = abs(m_move) / 0.15
            feats.extend([
                market_probs.get("home", 0.33),
                market_probs.get("draw", 0.33),
                market_probs.get("away", 0.33),
                getattr(ctx, "overround", 0.0) if hasattr(ctx, "overround") else 0.08,
                np.clip(m_move / 0.2, -1.0, 1.0),
                min(getattr(ctx, "source_count", 1) / 5.0, 1.0),
                np.clip(vol, 0.0, 1.0),
            ])
        else:
            feats.extend([0.33, 0.33, 0.33, 0.0, 0.0, 0.0, 0.0])

        # ─── E. Form (5) ───
        if form_features:
            feats.extend(form_features.to_list())
        else:
            feats.extend([0.33, 0.33, 0.0, 0.5, 0.0])

        # ─── F. H2H (6) ───
        if h2h_features:
            feats.extend(h2h_features.to_list())
        else:
            feats.extend([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])

        # ─── G. Meta (10) ───
        rest_h = getattr(ctx.home_team, "rest_days", 5)
        rest_a = getattr(ctx.away_team, "rest_days", 5)
        ref = getattr(ctx, "referee", None)

        ref_cardinality = (ref.yellow_cards_avg - 4.0) / 4.0 if ref else 0.0
        ref_home_bias = (ref.home_win_bias - 1.0) if ref else 0.0

        is_knockout = 1.0 if getattr(ctx, "is_knockout", False) else 0.0
        pressure = 0.8 if is_knockout else 0.2

        feats.extend([
            np.clip((rest_h - rest_a) / 7.0, -1.0, 1.0),
            is_knockout,
            0.0,  # is_derby
            ref_cardinality,
            ref_home_bias,
            np.clip(rest_h / 7.0, 0, 2.0),
            np.clip(rest_a / 7.0, 0, 2.0),
            1.0 if getattr(ctx, "is_late_season", False) else 0.0,
            pressure,
            0.0,  # is_prime_time
        ])

        if len(feats) != BASE_FEATURE_DIM:
            raise ValueError(
                f"FeatureBuilder base dim mismatch: got {len(feats)}, "
                f"expected {BASE_FEATURE_DIM}. Schema drift detected."
            )

        result = np.array([float(x) for x in feats], dtype=np.float32)

        if self.use_interactions:
            interactions = self._compute_interactions(result)
            if len(interactions) != INTERACTION_DIM:
                raise ValueError(
                    f"Interaction dim mismatch: got {len(interactions)}, "
                    f"expected {INTERACTION_DIM}"
                )
            result = np.concatenate([result, interactions])

        return result

    def _compute_interactions(self, feats: np.ndarray) -> np.ndarray:
        """5 个高价值交互项 — 顺序与 schema.INTERACTION_FEATURE_NAMES 一致。"""
        idx = {name: i for i, name in enumerate(BASE_FEATURE_NAMES)}
        inter = [
            feats[idx["elo_diff"]] * feats[idx["is_knockout"]],
            abs(feats[idx["poisson_win"]] - feats[idx["market_win"]]),
            feats[idx["momentum"]] * feats[idx["rest_advantage"]],
            feats[idx["market_win"]] * feats[idx["source_count"]],
            feats[idx["elo_diff"]] * feats[idx["form_win"]],
        ]
        return np.array(inter, dtype=np.float32)

    def get_input_dim(self) -> int:
        if self.use_interactions:
            return FULL_FEATURE_DIM
        return BASE_FEATURE_DIM

    def fit_scaler(self, feature_matrix: np.ndarray) -> None:
        self._feature_mean = np.mean(feature_matrix, axis=0)
        self._feature_std = np.std(feature_matrix, axis=0)
        self._feature_std[self._feature_std == 0] = 1.0

    def transform(self, features: np.ndarray) -> np.ndarray:
        if self._feature_mean is None:
            return features
        return (features - self._feature_mean) / self._feature_std
