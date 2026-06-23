"""
合成赔率数据源 — 基于 Elo 等级分差生成兜底赔率
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from database.models import Match, OddsHistory
from utils.logger import get_logger
from .base import OddsSnapshot, OddsSource

logger = get_logger("odds.synthetic")


class SyntheticOddsSource(OddsSource):
    """基于 Elo 等级分差生成合成赔率。当没有真实赔率数据源可用时，作为兜底方案。"""
    name = "synthetic"
    DEFAULT_OVERROUND = 1.08
    HOME_ADVANTAGE_ELO = 65

    def __init__(self, overround: float = None):
        self.overround = overround or self.DEFAULT_OVERROUND

    def _calc_probs(self, elo_home: float, elo_away: float) -> Tuple[float, float, float]:
        """基于 Elo 分差计算胜/平/负概率"""
        diff = elo_home - elo_away + self.HOME_ADVANTAGE_ELO
        p_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        p_draw = 0.28 * math.exp(-abs(diff) / 280.0)
        total = p_home + p_draw
        if total >= 1.0:
            p_home = p_home / total * 0.95
            p_draw = 0.05
        p_away = max(0.0, 1.0 - p_home - p_draw)
        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total

    def _probs_to_odds(self, p_home: float, p_draw: float, p_away: float) -> Tuple[float, float, float]:
        """概率转赔率，加入 overround"""
        return (
            round(1.0 / p_home * self.overround, 2) if p_home > 0 else 999.0,
            round(1.0 / p_draw * self.overround, 2) if p_draw > 0 else 999.0,
            round(1.0 / p_away * self.overround, 2) if p_away > 0 else 999.0,
        )

    def generate(self, match: Match) -> Optional[OddsSnapshot]:
        """为比赛生成合成赔率"""
        home_elo = match.home_team.elo if match.home_team else 1500
        away_elo = match.away_team.elo if match.away_team else 1500
        if not home_elo or not away_elo:
            return None

        p_h, p_d, p_a = self._calc_probs(home_elo, away_elo)
        o_h, o_d, o_a = self._probs_to_odds(p_h, p_d, p_a)

        return OddsSnapshot(
            match_id=match.id, source=self.name,
            odds_home=o_h, odds_draw=o_d, odds_away=o_a,
            recorded_at=datetime.now(timezone.utc),
        )

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        return self.generate(match)

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return [snap for m in matches if (snap := self.generate(m))]
