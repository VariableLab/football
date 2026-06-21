"""教练临场能力模型 — 高评分教练在关键时刻的提升效应。"""

from core.context import TeamContext


class CoachImpactModel:
    """高评分教练在关键时刻能提升球队进攻效率 3~6%。"""

    @classmethod
    def compute_factor(cls, team: TeamContext, is_knockout: bool = False) -> float:
        rating = team.coach_rating
        base = 1.0 + 0.04 * (rating - 0.5)
        if is_knockout:
            base += 0.03 * (rating - 0.5)
        return max(0.90, min(1.10, base))
