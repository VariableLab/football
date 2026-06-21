"""球员状态修正模型 — 根据球员可用性和疲劳度输出战力修正系数。"""

from typing import Tuple

from core.context import MatchContext, TeamContext


class PlayerAdjustmentModel:
    """根据球员 availability 和疲劳度，输出一个战力修正系数。"""

    POSITION_IMPACT = {
        "goalkeeper": 0.12,
        "defense": 0.08,
        "midfield": 0.07,
        "forward": 0.08,
    }

    @classmethod
    def predict(cls, ctx: MatchContext) -> float:
        """返回 0.7 ~ 1.3 的战力修正系数"""
        home = ctx.home_team
        away = ctx.away_team

        home_avail = home.key_players_available / max(home.key_players_total, 1)
        away_avail = away.key_players_available / max(away.key_players_total, 1)

        home_fatigue_penalty = home.squad_fatigue_index * 0.10
        away_fatigue_penalty = away.squad_fatigue_index * 0.10

        home_strength = home_avail * (1 - home_fatigue_penalty)
        away_strength = away_avail * (1 - away_fatigue_penalty)

        if away_strength == 0:
            return 1.3
        ratio = home_strength / away_strength
        return max(0.7, min(1.3, 0.85 + 0.15 * ratio))
