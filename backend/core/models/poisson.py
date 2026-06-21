"""泊松双变量模型 — 比分/胜平负/让球/总进球/半全场预测。

使用 Dixon-Coles 修正替代独立泊松，更准确地校准低比分概率。
"""

from typing import Any, Dict, Tuple

import numpy as np
from scipy.stats import poisson

from core.context import MatchContext
from core.constants import (
    MAX_GOALS, DIXON_COLES_RHO, DRAW_INFLATION_FACTOR,
)
from features.adjustment_models import RefereeModel
from core.models.form_adjustment import FormAdjustmentModel
from core.models.home_away import HomeAwayModel
from core.models.schedule_density import ScheduleDensityModel
from core.models.weather_venue import WeatherVenueModel
from core.models.tactical import TacticalModel
from core.models.coach_impact import CoachImpactModel
from core.models.squad_availability import SquadAvailabilityModel


class PoissonModel:
    """双变量泊松模型，输出比分/胜平负/让球/总进球/半全场概率。"""

    @classmethod
    def _compute_lambdas(cls, ctx: MatchContext) -> Tuple[float, float]:
        """计算双方期望进球 λ（全维度修正版）"""
        home_attack = ctx.home_team.avg_xg if ctx.home_team.avg_xg > 0 else ctx.home_team.avg_goals_scored
        home_defense = ctx.home_team.avg_xga if ctx.home_team.avg_xga > 0 else ctx.home_team.avg_goals_conceded
        away_attack = ctx.away_team.avg_xg if ctx.away_team.avg_xg > 0 else ctx.away_team.avg_goals_scored
        away_defense = ctx.away_team.avg_xga if ctx.away_team.avg_xga > 0 else ctx.away_team.avg_goals_conceded

        if ctx.home_team.tournament_matches_played > 0:
            home_attack = 0.6 * home_attack + 0.4 * (
                ctx.home_team.tournament_goals_scored / max(ctx.home_team.tournament_matches_played, 1)
            )
        if ctx.away_team.tournament_matches_played > 0:
            away_attack = 0.6 * away_attack + 0.4 * (
                ctx.away_team.tournament_goals_scored / max(ctx.away_team.tournament_matches_played, 1)
            )

        neutral_advantage = 1.0
        if ctx.home_team.fifa_rank < ctx.away_team.fifa_rank:
            neutral_advantage = 1.05
        elif ctx.home_team.fifa_rank > ctx.away_team.fifa_rank:
            neutral_advantage = 0.95

        lambda_home = home_attack * away_defense * ctx.home_team.form_factor * neutral_advantage
        lambda_away = away_attack * home_defense * ctx.away_team.form_factor * (1.0 / neutral_advantage)

        lambda_home *= FormAdjustmentModel.compute_factor(ctx.home_team)
        lambda_away *= FormAdjustmentModel.compute_factor(ctx.away_team)

        lambda_home *= HomeAwayModel.compute_factor(ctx, is_home=True)
        lambda_away *= HomeAwayModel.compute_factor(ctx, is_home=False)

        lambda_home *= ScheduleDensityModel.compute_factor(ctx.home_team)
        lambda_away *= ScheduleDensityModel.compute_factor(ctx.away_team)

        lambda_home *= WeatherVenueModel.compute_factor(ctx, ctx.home_team)
        lambda_away *= WeatherVenueModel.compute_factor(ctx, ctx.away_team)

        tact_h, tact_a = TacticalModel.compute_factors(ctx)
        lambda_home *= tact_h
        lambda_away *= tact_a

        lambda_home *= CoachImpactModel.compute_factor(ctx.home_team, ctx.is_knockout)
        lambda_away *= CoachImpactModel.compute_factor(ctx.away_team, ctx.is_knockout)

        home_atk_pen, home_def_pen = SquadAvailabilityModel.compute_factor(ctx.home_team)
        away_atk_pen, away_def_pen = SquadAvailabilityModel.compute_factor(ctx.away_team)
        lambda_home *= home_atk_pen * away_def_pen
        lambda_away *= away_atk_pen * home_def_pen

        ref_h, ref_a = RefereeModel.compute_factor(ctx)
        lambda_home *= ref_h
        lambda_away *= ref_a

        if ctx.is_knockout:
            stage_factor = {"R16": 0.88, "QF": 0.85, "SF": 0.82, "F": 0.80, "3P": 0.90}
            factor = stage_factor.get(ctx.stage, 0.85)
            lambda_home *= factor
            lambda_away *= factor

        if ctx.is_third_round_group:
            if ctx.home_team_qualified is True or ctx.away_team_qualified is True:
                lambda_home *= 0.90
                lambda_away *= 0.90

        return max(lambda_home, 0.1), max(lambda_away, 0.1)

    @staticmethod
    def _tau_dixon_coles(i: int, j: int, lambda_h: float, lambda_a: float, rho: float) -> float:
        """Dixon-Coles 相关性修正因子 tau(i,j)。"""
        if i == 0 and j == 0:
            return 1.0 - lambda_h * lambda_a * rho
        elif i == 1 and j == 0:
            return 1.0 + lambda_h * rho
        elif i == 0 and j == 1:
            return 1.0 + lambda_a * rho
        elif i == 1 and j == 1:
            return 1.0 - rho
        return 1.0

    @classmethod
    def predict_score_matrix(cls, ctx: MatchContext, rho: float = DIXON_COLES_RHO) -> Tuple[np.ndarray, float, float]:
        """返回 (MAX_GOALS+1) × (MAX_GOALS+1) 的比分概率矩阵。"""
        lambda_h, lambda_a = cls._compute_lambdas(ctx)
        size = MAX_GOALS + 1
        matrix = np.zeros((size, size))

        for i in range(size):
            for j in range(size):
                if i < MAX_GOALS and j < MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * poisson.pmf(j, lambda_a)
                elif i == MAX_GOALS and j < MAX_GOALS:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * poisson.pmf(j, lambda_a)
                elif i < MAX_GOALS and j == MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))
                else:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))

                tau = cls._tau_dixon_coles(i, j, lambda_h, lambda_a, rho)
                matrix[i][j] = tau * base

        total = matrix.sum()
        if total > 0:
            matrix /= total
        return matrix, lambda_h, lambda_a

    @classmethod
    def predict_score_matrix_with_lambdas(cls, lambda_h: float, lambda_a: float, rho: float = DIXON_COLES_RHO) -> Tuple[np.ndarray, float, float]:
        """直接使用传入的 lambda_h, lambda_a 构建 Dixon-Coles 比分概率矩阵。"""
        size = MAX_GOALS + 1
        matrix = np.zeros((size, size))

        for i in range(size):
            for j in range(size):
                if i < MAX_GOALS and j < MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * poisson.pmf(j, lambda_a)
                elif i == MAX_GOALS and j < MAX_GOALS:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * poisson.pmf(j, lambda_a)
                elif i < MAX_GOALS and j == MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))
                else:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))

                tau = cls._tau_dixon_coles(i, j, lambda_h, lambda_a, rho)
                matrix[i][j] = tau * base

        total = matrix.sum()
        if total > 0:
            matrix /= total
        return matrix, lambda_h, lambda_a

    @classmethod
    def predict_spf_only(cls, ctx: MatchContext) -> Dict[str, float]:
        """只计算胜平负概率，跳过比分/进球等（用于权重学习加速）"""
        lambda_h, lambda_a = cls._compute_lambdas(ctx)
        size = MAX_GOALS + 1

        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0

        for i in range(size):
            for j in range(size):
                if i < MAX_GOALS and j < MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * poisson.pmf(j, lambda_a)
                elif i == MAX_GOALS and j < MAX_GOALS:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * poisson.pmf(j, lambda_a)
                elif i < MAX_GOALS and j == MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))
                else:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))

                tau = cls._tau_dixon_coles(i, j, lambda_h, lambda_a, DIXON_COLES_RHO)
                prob = tau * base

                if i > j:
                    p_home += prob
                elif i == j:
                    p_draw += prob
                else:
                    p_away += prob

        p_draw *= DRAW_INFLATION_FACTOR
        total = p_home + p_draw + p_away
        return {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}

    @classmethod
    def predict(cls, ctx: MatchContext) -> Dict[str, Any]:
        """返回泊松模型的全部玩法预测"""
        matrix, lambda_h, lambda_a = cls.predict_score_matrix(ctx)
        size = matrix.shape[0]

        # 1. 胜平负
        p_home = sum(matrix[i][j] for i in range(size) for j in range(size) if i > j)
        p_draw = sum(matrix[i][j] for i in range(size) for j in range(size) if i == j)
        p_away = sum(matrix[i][j] for i in range(size) for j in range(size) if i < j)

        p_draw *= DRAW_INFLATION_FACTOR
        total = p_home + p_draw + p_away
        spf = {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}

        # 2. 比分
        inflated_matrix = np.copy(matrix)
        for i in range(size):
            inflated_matrix[i][i] *= DRAW_INFLATION_FACTOR
        inflated_sum = inflated_matrix.sum()
        if inflated_sum > 0:
            inflated_matrix /= inflated_sum

        score = {}
        for i in range(size):
            for j in range(size):
                key = f"{i}:{j}" if i < MAX_GOALS and j < MAX_GOALS else f"{min(i, MAX_GOALS)}+:{min(j, MAX_GOALS)}+"
                prob = inflated_matrix[i][j]
                if prob > 0.01:
                    score[f"{i}:{j}"] = round(prob, 4)

        # 3. 总进球
        goals = {}
        for total_goals in range(7):
            prob = sum(matrix[i][j] for i in range(size) for j in range(size) if i + j == total_goals)
            if prob > 0.005:
                goals[str(total_goals)] = round(prob, 4)
        prob_7plus = sum(matrix[i][j] for i in range(size) for j in range(size) if i + j >= 7)
        if prob_7plus > 0.005:
            goals["7+"] = round(prob_7plus, 4)

        # 4. 让球胜平负
        handicap = ctx.handicap
        p_rq_home = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) > handicap)
        p_rq_draw = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) == handicap)
        p_rq_away = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) < handicap)
        rq_total = p_rq_home + p_rq_draw + p_rq_away

        rq = {
            "home": p_rq_home / rq_total,
            "draw": p_rq_draw / rq_total,
            "away": p_rq_away / rq_total,
            "handicap": handicap,
        }

        # 5. 半全场
        HT_FT_TRANSITION = {
            "home":   {"home": 0.785, "draw": 0.151, "away": 0.065},
            "draw":   {"home": 0.442, "draw": 0.237, "away": 0.321},
            "away":   {"home": 0.105, "draw": 0.199, "away": 0.697},
        }
        HT_DISTRIBUTION = {"home": 0.368, "draw": 0.364, "away": 0.268}

        lambda_h_1h = lambda_h * 0.48
        lambda_a_1h = lambda_a * 0.48

        def half_outcome_prob(lh: float, la: float) -> Dict[str, float]:
            """计算半场结果概率"""
            p_h = 0.0
            p_d = 0.0
            p_a = 0.0
            for i in range(5):
                for j in range(5):
                    pi = poisson.pmf(i, lh)
                    pj = poisson.pmf(j, la)
                    if i > j:
                        p_h += pi * pj
                    elif i == j:
                        p_d += pi * pj
                    else:
                        p_a += pi * pj
            t = p_h + p_d + p_a
            return {"home": p_h / t, "draw": p_d / t, "away": p_a / t}

        half_1h = half_outcome_prob(lambda_h_1h, lambda_a_1h)

        for k in half_1h:
            half_1h[k] = 0.5 * half_1h[k] + 0.5 * HT_DISTRIBUTION[k]

        half = {}
        outcomes = ["home", "draw", "away"]
        labels = {"homehome": "主主", "homedraw": "主平", "homeaway": "主客",
                  "drawhome": "平主", "drawdraw": "平平", "drawaway": "平客",
                  "awayhome": "客主", "awaydraw": "客平", "awayaway": "客客"}
        for h1 in outcomes:
            for h2 in outcomes:
                key = f"{h1}{h2}"
                prob = half_1h[h1] * HT_FT_TRANSITION[h1][h2]
                half[labels.get(key, key)] = prob

        half_total = sum(half.values())
        half = {k: round(v / half_total, 4) for k, v in half.items()}

        return {
            "spf": spf,
            "rq": rq,
            "score": score,
            "goals": goals,
            "half": half,
            "lambda_home": lambda_h,
            "lambda_away": lambda_a,
        }
