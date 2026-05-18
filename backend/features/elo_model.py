"""
Elo 实力基线模型

Elo 评分系统，输出胜平负概率。
参考 FiveThirtyEight 的 Soccer SPI 方法，加入平局修正。

世界杯经验参数：Elo 差对应的胜率
"""
import math
from typing import Dict


# 世界杯是中立场
HOME_ADVANTAGE_ELO = 0


class EloModel:
    """Elo 评分系统 — 输出实力基线胜平负概率"""

    @staticmethod
    def win_prob(elo_diff: float) -> float:
        """Elo 差 → 胜率"""
        return 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

    @classmethod
    def predict(cls, ctx: "MatchContext") -> Dict[str, float]:
        """输入 MatchContext，输出 {"home", "draw", "away"} 概率"""
        diff = ctx.home_team.elo - ctx.away_team.elo + HOME_ADVANTAGE_ELO

        # 基础胜率
        p_win = cls.win_prob(diff)
        p_loss = cls.win_prob(-diff)

        # 平局修正：Elo 接近时平局概率上升
        # 经验公式：平局概率 ~ 0.25 + 0.10 * exp(-|diff|/200)
        draw_base = 0.25 + 0.10 * math.exp(-abs(diff) / 200.0)

        # 淘汰赛平局概率更高（保守）
        if ctx.is_knockout:
            draw_base += 0.08

        # 归一化
        total = p_win + draw_base + p_loss
        return {
            "home": p_win / total,
            "draw": draw_base / total,
            "away": p_loss / total,
        }
