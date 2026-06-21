"""赛程密度/疲劳修正模型 — 根据休息天数和疲劳指数计算修正系数。"""

from core.context import TeamContext


class ScheduleDensityModel:
    """休息天数不足 → 进攻/防守效率下降。"""

    @classmethod
    def compute_factor(cls, team: TeamContext) -> float:
        # rest_days 可能在 MatchContext 上,不在 TeamContext 上
        rest = getattr(team, "rest_days", 7)
        fatigue = team.squad_fatigue_index

        rest_penalty = {
            (5, 999): 1.00,
            (3, 5): 0.97,
            (2, 3): 0.92,
            (0, 2): 0.85,
        }
        rest_mult = 0.85
        for (low, high), val in rest_penalty.items():
            if low <= rest < high:
                rest_mult = val
                break

        fatigue_mult = 1.0 - 0.15 * fatigue

        return max(0.80, rest_mult * fatigue_mult)
