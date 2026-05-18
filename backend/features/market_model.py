"""
市场赔率模型 — 从赔率反推隐含概率

使用 multiplicative method 去除博彩公司抽水 (overround)。
竞彩返奖率 ~71%，欧洲主流博彩返奖率 ~92-95%。
"""
from typing import Dict, Optional


class MarketModel:
    """
    从市场赔率反推隐含概率。
    使用基础归一化（去除抽水），保留市场原始信号强度。
    """

    @classmethod
    def predict(cls, ctx: "MatchContext") -> Optional[Dict[str, float]]:
        """
        只在存在真实收盘赔率或普通赔率时才输出概率。
        合成赔率（synthetic）会被跳过，避免循环引用。
        """
        if ctx.has_closing_odds:
            o1, oX, o2 = ctx.closing_odds_home, ctx.closing_odds_draw, ctx.closing_odds_away
        elif ctx.has_odds:
            o1, oX, o2 = ctx.odds_home, ctx.odds_draw, ctx.odds_away
        else:
            return None

        # === Multiplicative 去水 ===
        # overround = Σ(1/odds_i)，竞彩约 1.40，欧洲约 1.07
        # P_i = (1/odds_i) / overround
        raw = {"home": 1.0 / o1, "draw": 1.0 / oX, "away": 1.0 / o2}
        total = sum(raw.values())

        # 轻微平滑（5% 均匀分布），防止极端赔率导致的过自信
        uniform = 1.0 / 3.0
        result = {}
        for k in raw:
            prob = raw[k] / total
            result[k] = 0.95 * prob + 0.05 * uniform

        # 再归一化
        t2 = sum(result.values())
        return {k: v / t2 for k, v in result.items()}

    @staticmethod
    def implied_probability(odds_home: float, odds_draw: float, odds_away: float) -> Dict[str, float]:
        """
        纯函数版本：给定三个赔率，直接返回去水后隐含概率。
        不依赖 MatchContext，适合在数据同步时调用。
        """
        raw = {"home": 1.0 / odds_home, "draw": 1.0 / odds_draw, "away": 1.0 / odds_away}
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}
