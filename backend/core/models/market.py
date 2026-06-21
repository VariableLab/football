"""市场赔率隐含概率模型 — 从市场赔率反推隐含概率。"""

from typing import Dict, Optional

from core.context import MatchContext


class MarketModel:
    """从市场赔率反推隐含概率。"""

    @classmethod
    def predict(cls, ctx: MatchContext) -> Optional[Dict[str, float]]:
        if ctx.has_closing_odds:
            o1, oX, o2 = ctx.closing_odds_home, ctx.closing_odds_draw, ctx.closing_odds_away
        elif ctx.has_odds:
            o1, oX, o2 = ctx.odds_home, ctx.odds_draw, ctx.odds_away
        else:
            return None

        raw = {"home": 1.0 / o1, "draw": 1.0 / oX, "away": 1.0 / o2}
        total = sum(raw.values())

        uniform = 1.0 / 3.0
        result = {}
        for k in raw:
            prob = raw[k] / total
            result[k] = 0.95 * prob + 0.05 * uniform

        t2 = sum(result.values())
        return {k: v / t2 for k, v in result.items()}
