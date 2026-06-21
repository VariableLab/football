"""阵容完整度模型 — 核心球员伤停对攻防的影响。"""

from typing import Tuple

from core.context import TeamContext


class SquadAvailabilityModel:
    """核心球员伤停对攻防的影响。"""

    @classmethod
    def compute_factor(cls, team: TeamContext) -> Tuple[float, float]:
        """返回 (进攻乘数, 防守乘数)"""
        if not getattr(team, "key_injuries", ""):
            return 1.0, 1.0

        injuries = [x.strip() for x in team.key_injuries.split(",") if x.strip()]
        count = len(injuries)

        attack_pen = min(0.15, count * 0.03)
        defense_pen = min(0.12, count * 0.02)

        fatigue_mult = 1.0 + 0.5 * team.squad_fatigue_index
        attack_pen *= fatigue_mult
        defense_pen *= fatigue_mult

        return max(0.80, 1.0 - attack_pen), max(0.85, 1.0 - defense_pen)
