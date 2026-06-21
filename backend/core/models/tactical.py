"""战术风格相克模型 — 不同战术风格相遇时的进球预期修正。"""

from typing import Tuple

from core.context import MatchContext


class TacticalModel:
    """不同战术风格相遇时的进球预期修正。"""

    TACTICAL_MATRIX = {
        ("attack", "attack"): (1.15, 1.15),
        ("attack", "defense"): (0.88, 0.72),
        ("attack", "balanced"): (1.02, 0.92),
        ("attack", "counter"): (0.95, 1.12),
        ("defense", "attack"): (0.72, 0.88),
        ("defense", "defense"): (0.68, 0.68),
        ("defense", "balanced"): (0.80, 0.85),
        ("defense", "counter"): (0.62, 0.75),
        ("balanced", "attack"): (0.92, 1.02),
        ("balanced", "defense"): (0.85, 0.80),
        ("balanced", "balanced"): (1.00, 1.00),
        ("balanced", "counter"): (0.95, 0.95),
        ("counter", "attack"): (1.12, 0.95),
        ("counter", "defense"): (0.75, 0.62),
        ("counter", "balanced"): (0.95, 0.95),
        ("counter", "counter"): (0.85, 0.85),
    }

    @classmethod
    def compute_factors(cls, ctx: MatchContext) -> Tuple[float, float]:
        home_s = (ctx.home_team.tactical_style or "balanced").lower()
        away_s = (ctx.away_team.tactical_style or "balanced").lower()
        base = cls.TACTICAL_MATRIX.get((home_s, away_s), (1.0, 1.0))

        h_poss = ctx.home_team.possession
        a_poss = ctx.away_team.possession
        if h_poss > 0 and a_poss > 0:
            poss_diff = (h_poss - a_poss) / 100.0
            adj = poss_diff * 0.15
            return (base[0] * (1 + adj), base[1] * (1 - adj))
        return base
