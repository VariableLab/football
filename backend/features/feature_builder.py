"""
特征拼接器 — FeatureBuilder

将所有 Layer 1 子模型输出拼接为一个 38 维特征向量，
供 Layer 2 逻辑回归融合使用。

特征清单 (38 维):

A. Elo (7维):    elo_diff, elo_win, elo_draw, elo_away, heavy_fav, heavy_udog, elo_tier_diff
B. Poisson (7维): lambda_home, lambda_away, lambda_diff, poisson_win, poisson_draw, poisson_away, goal_exp
C. Players (4维): home_avail, away_avail, avail_diff, injury_impact
D. Market (6维):  market_win, market_draw, market_away, overround, max_odds_move, source_count
E. Form (5维):    form_win, form_draw, momentum, stability, streak_norm
F. H2H (6维):     h2h_total_norm, h2h_win, h2h_draw, h2h_recent, h2h_goals_norm, first_meeting
G. Meta (3维):    rest_advantage, is_knockout, is_derby

交互特征 (自动生成 ~10 维):
  elo_diff × is_knockout
  market_win × source_count
  ...
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

from logger import get_logger

logger = get_logger("feature_builder")

# ─── 特征维度 ───
FEATURE_DIM = 38
FEATURE_NAMES = [
    # A. Elo (7)
    "elo_diff", "elo_win", "elo_draw", "elo_away",
    "is_heavy_fav", "is_heavy_udog", "elo_tier_diff",
    # B. Poisson (7)
    "lambda_home", "lambda_away", "lambda_diff",
    "poisson_win", "poisson_draw", "poisson_away", "goal_exp",
    # C. Players (4)
    "home_avail", "away_avail", "avail_diff", "injury_impact",
    # D. Market (6)
    "market_win", "market_draw", "market_away",
    "overround", "max_odds_move", "source_count",
    # E. Form (5)
    "form_win", "form_draw", "momentum", "stability", "streak_norm",
    # F. H2H (6)
    "h2h_total_norm", "h2h_win", "h2h_draw", "h2h_recent", "h2h_goals_norm", "first_meeting",
    # G. Meta (3)
    "rest_advantage", "is_knockout", "is_derby",
]


class FeatureBuilder:
    """
    特征拼接器。

    用法:
        builder = FeatureBuilder()
        features = builder.build(
            elo_probs, poisson_result, players_factor, market_probs,
            form_features, h2h_features, ctx
        )
        # features.shape → (38,)
        # 可直接喂给 logistic_fusion.predict()
    """

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
        拼接 38 维特征向量。

        Returns:
            np.ndarray shape (38,), dtype float32, 已归一化到 ~[0,1] 或 ~[-1,1]
        """
        feats = []

        # ─── A. Elo (7) ───
        elo_home = getattr(ctx.home_team, "elo", 1500)
        elo_away = getattr(ctx.away_team, "elo", 1500)
        elo_diff = (elo_home - elo_away) / 400.0         # 归一化
        feats.extend([
            np.clip(elo_diff, -2.0, 2.0),
            elo_probs.get("home", 0.33),
            elo_probs.get("draw", 0.33),
            elo_probs.get("away", 0.33),
            1.0 if elo_diff > 0.5 else 0.0,              # heavy favorite
            1.0 if elo_diff < -0.5 else 0.0,             # heavy underdog
            np.clip((elo_home - 1400) / 600.0, -1.0, 1.0),  # Elo tier
        ])

        # ─── B. Poisson (7) ───
        lam_h = poisson_result.get("lambda_home", 1.2)
        lam_a = poisson_result.get("lambda_away", 1.0)
        poisson_spf = poisson_result.get("spf", {})
        feats.extend([
            np.clip(lam_h / 3.0, 0.0, 1.0),
            np.clip(lam_a / 3.0, 0.0, 1.0),
            np.clip((lam_h - lam_a) / 2.0, -1.0, 1.0),
            poisson_spf.get("home", 0.33),
            poisson_spf.get("draw", 0.33),
            poisson_spf.get("away", 0.33),
            np.clip((lam_h + lam_a) / 5.0, 0.0, 1.0),    # expected total goals
        ])

        # ─── C. Players (4) ───
        h_avail = getattr(ctx.home_team, "key_players_available", 11) / max(getattr(ctx.home_team, "key_players_total", 11), 1)
        a_avail = getattr(ctx.away_team, "key_players_available", 11) / max(getattr(ctx.away_team, "key_players_total", 11), 1)
        h_inj = len((getattr(ctx.home_team, "key_injuries", "") or "").split(",")) if getattr(ctx.home_team, "key_injuries", "") else 0
        a_inj = len((getattr(ctx.away_team, "key_injuries", "") or "").split(",")) if getattr(ctx.away_team, "key_injuries", "") else 0
        feats.extend([
            h_avail,
            a_avail,
            h_avail - a_avail,
            min((h_inj + a_inj) / 10.0, 1.0),  # injury impact
        ])

        # ─── D. Market (6) ───
        if market_probs:
            feats.extend([
                market_probs.get("home", 0.33),
                market_probs.get("draw", 0.33),
                market_probs.get("away", 0.33),
                getattr(ctx, "overround", 0.0) if hasattr(ctx, "overround") else 0.08,
                0.0,  # max_odds_move (需要时序比较，暂填0)
                min(getattr(ctx, "source_count", 1) / 5.0, 1.0),
            ])
        else:
            feats.extend([0.33, 0.33, 0.33, 0.0, 0.0, 0.0])

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

        # ─── G. Meta (3) ───
        rest_h = getattr(ctx.home_team, "rest_days", 5)
        rest_a = getattr(ctx.away_team, "rest_days", 5)
        feats.extend([
            np.clip((rest_h - rest_a) / 7.0, -1.0, 1.0),  # rest advantage
            1.0 if getattr(ctx, "is_knockout", False) else 0.0,
            0.0,  # is_derby (暂未实现)
        ])

        result = np.array(feats, dtype=np.float32)

        # 交互特征（简单实现：选取关键交互项）
        if self.use_interactions and len(result) >= 38:
            interactions = self._compute_interactions(result)
            result = np.concatenate([result, interactions])

        return result

    def _compute_interactions(self, feats: np.ndarray) -> np.ndarray:
        """
        计算交互特征。
        当前选取 5 个高价值交互项。
        """
        inter = []
        idx = {name: i for i, name in enumerate(FEATURE_NAMES)}

        # 1. elo_diff × is_knockout
        if "elo_diff" in idx and "is_knockout" in idx:
            inter.append(feats[idx["elo_diff"]] * feats[idx["is_knockout"]])

        # 2. poisson_win × market_win (模型一致性)
        if "poisson_win" in idx and "market_win" in idx:
            inter.append(abs(feats[idx["poisson_win"]] - feats[idx["market_win"]]))

        # 3. momentum × rest_advantage
        if "momentum" in idx and "rest_advantage" in idx:
            inter.append(feats[idx["momentum"]] * feats[idx["rest_advantage"]])

        # 4. market_win × source_count
        if "market_win" in idx and "source_count" in idx:
            inter.append(feats[idx["market_win"]] * feats[idx["source_count"]])

        # 5. elo_diff × form_win
        if "elo_diff" in idx and "form_win" in idx:
            inter.append(feats[idx["elo_diff"]] * feats[idx["form_win"]])

        return np.array(inter, dtype=np.float32)

    def get_input_dim(self) -> int:
        """返回特征向量总维度（含交互特征）"""
        base = FEATURE_DIM
        if self.use_interactions:
            base += 5  # 5 个交互特征
        return base

    def fit_scaler(self, feature_matrix: np.ndarray) -> None:
        """在训练集上拟合标准化参数"""
        self._feature_mean = np.mean(feature_matrix, axis=0)
        self._feature_std = np.std(feature_matrix, axis=0)
        self._feature_std[self._feature_std == 0] = 1.0  # 防止除零

    def transform(self, features: np.ndarray) -> np.ndarray:
        """标准化特征"""
        if self._feature_mean is None:
            return features
        return (features - self._feature_mean) / self._feature_std
