"""主客场修正模型 — 处理主客场差异和气候/时差适应优势。"""

from core.context import MatchContext


class HomeAwayModel:
    """主客场差异修正。"""

    @classmethod
    def compute_factor(cls, ctx: MatchContext, is_home: bool) -> float:
        if ctx.venue_type == "neutral":
            return 1.0

        team = ctx.home_team if is_home else ctx.away_team
        factor = team.home_away_factor
        if is_home:
            return factor
        return max(0.8, 2.0 - factor)
