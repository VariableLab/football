"""
特征 schema 单一真相源 (Single Source of Truth)

所有训练/推理路径必须与此对齐:
  - 基线 BASE_FEATURE_DIM = 48
  - 交互 INTERACTION_DIM = 5
  - 全量 FULL_FEATURE_DIM = 53  (use_interactions=True)

权威 LR 权重: global_v1_2026-05-25.json (dim=48, acc≈57.4%)
维度变更必须同步重训 LR，禁止静默漂移。
"""
from __future__ import annotations

from typing import List, Tuple

# ─── 基线 48 维 ───
BASE_FEATURE_NAMES: List[str] = [
    # A. Elo (8)
    "elo_diff", "elo_win", "elo_draw", "elo_away",
    "is_heavy_fav", "is_heavy_udog", "elo_tier_diff", "elo_drift",
    # B. Poisson (8)
    "lambda_home", "lambda_away", "lambda_diff",
    "poisson_win", "poisson_draw", "poisson_away", "goal_exp", "relative_goals",
    # C. Players (4)
    "home_avail", "away_avail", "avail_diff", "injury_impact",
    # D. Market (7)
    "market_win", "market_draw", "market_away",
    "overround", "max_odds_move", "source_count", "market_volatility",
    # E. Form (5)
    "form_win", "form_draw", "momentum", "stability", "streak_norm",
    # F. H2H (6)
    "h2h_total_norm", "h2h_win", "h2h_draw", "h2h_recent", "h2h_goals_norm", "first_meeting",
    # G. Meta (10)
    "rest_advantage", "is_knockout", "is_derby", "ref_cardinality", "ref_home_bias",
    "home_rest", "away_rest", "is_late_season", "pressure_index", "is_prime_time",
]

INTERACTION_FEATURE_NAMES: List[str] = [
    "I_elo_knockout",
    "I_poisson_market_consistent",
    "I_momentum_rest",
    "I_market_source",
    "I_elo_form",
]

BASE_FEATURE_DIM: int = len(BASE_FEATURE_NAMES)  # 48
INTERACTION_DIM: int = len(INTERACTION_FEATURE_NAMES)  # 5
FULL_FEATURE_DIM: int = BASE_FEATURE_DIM + INTERACTION_DIM  # 53

# 向后兼容别名
FEATURE_DIM = BASE_FEATURE_DIM
FEATURE_NAMES = BASE_FEATURE_NAMES + INTERACTION_FEATURE_NAMES  # 53 全量名称


def assert_schema_integrity() -> None:
    """启动/测试时断言 schema 自洽。"""
    assert BASE_FEATURE_DIM == 48, f"BASE must be 48, got {BASE_FEATURE_DIM}"
    assert INTERACTION_DIM == 5, f"INTERACTION must be 5, got {INTERACTION_DIM}"
    assert len(FEATURE_NAMES) == FULL_FEATURE_DIM
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES), "duplicate feature names"


def describe() -> Tuple[int, int, int]:
    return BASE_FEATURE_DIM, INTERACTION_DIM, FULL_FEATURE_DIM
