"""Elo 实力模型 — 基于 Elo 评分的胜平负概率预测。

参考 FiveThirtyEight 的 Soccer SPI 方法，加入平局修正。
"""

import math
from typing import Dict

from core.constants import HOME_ADVANTAGE_ELO
from core.context import MatchContext


class EloModel:
    """Elo 评分系统，输出胜平负概率。"""

    @staticmethod
    def win_prob(elo_diff: float) -> float:
        return 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

    @classmethod
    def predict(cls, ctx: MatchContext) -> Dict[str, float]:
        home_advantage = 0 if ctx.venue_type == "neutral" else HOME_ADVANTAGE_ELO
        diff = ctx.home_team.elo - ctx.away_team.elo + home_advantage
        p_win = cls.win_prob(diff)
        p_loss = cls.win_prob(-diff)
        draw_base = 0.25 + 0.10 * math.exp(-abs(diff) / 200.0)
        if ctx.is_knockout:
            draw_base += 0.08
        total = p_win + draw_base + p_loss
        return {
            "home": p_win / total,
            "draw": draw_base / total,
            "away": p_loss / total,
        }
