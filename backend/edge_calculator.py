"""
边际计算器 — 计算模型 vs 市场的优势边际。

核心逻辑: 博彩公司赔率隐含的概率 + 模型校准后的概率 之间的差值
就是边际 (edge)。只有边际 > 抽水率时, 投注才有长期正收益。

竞彩抽水率约 8-12%, 欧洲主流公司约 4-6%。边际必须超过抽水率
加安全垫才是真正有价值的投注机会。

用法:
    from edge_calculator import EdgeCalculator
    ec = EdgeCalculator()
    result = ec.compute(odds_home=1.80, odds_draw=3.50, odds_away=4.20,
                        calibrated_probs={"home": 0.58, "draw": 0.24, "away": 0.18})
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class EdgeResult:
    """单选项的边际计算结果"""
    selection: str
    calibrated_prob: float
    market_prob: float
    edge: float           # calibrated_prob - market_prob
    ev: float             # calibrated_prob × odds - 1
    odds: float
    is_value: bool        # edge > 0 且 ev > 0


@dataclass(frozen=True)
class MatchEdgeResult:
    """整场比赛的边际计算结果"""
    edges: Dict[str, EdgeResult]
    overround_pct: float   # 抽水率 (%)
    min_profitable_edge: float  # 最小盈利边际
    best_selection: Optional[str]  # 最大正边际选项


class EdgeCalculator:
    """
    边际计算器。

    用法:
        ec = EdgeCalculator()
        result = ec.compute(1.80, 3.50, 4.20, {"home": 0.58, "draw": 0.24, "away": 0.18})
        print(result.best_selection)  # 边际最大的选项
    """

    # 竞彩抽水率 (典型值 8-12%, 取保守估计 10%)
    JINGCAI_OVERROUND = 0.129  # 真实数据: sporttery.cn 55场平均12.9%
    # 欧洲公司抽水率 (典型值 4-6%)
    EUROPEAN_OVERROUND = 0.05
    # 安全垫: 最小盈利边际需要额外 2%
    SAFETY_MARGIN = 0.02

    def overround(self, odds_home: float, odds_draw: float, odds_away: float) -> float:
        """计算抽水率 (overround)。值越高, 庄家利润越多。"""
        if odds_home <= 1.0 or odds_draw <= 1.0 or odds_away <= 1.0:
            return 0.0
        return (1.0 / odds_home + 1.0 / odds_draw + 1.0 / odds_away - 1.0)

    def market_implied_probs(
        self,
        odds_home: float,
        odds_draw: float,
        odds_away: float,
    ) -> Dict[str, float]:
        """
        计算市场隐含概率 (去除返水后)。

        原始隐含概率: 1/odds, 总和 > 1 (多出的部分就是庄家利润)
        去返水: implied / sum(implied)
        """
        if odds_home <= 1.0 or odds_draw <= 1.0 or odds_away <= 1.0:
            return {"home": 1/3, "draw": 1/3, "away": 1/3}

        raw_home = 1.0 / odds_home
        raw_draw = 1.0 / odds_draw
        raw_away = 1.0 / odds_away
        total = raw_home + raw_draw + raw_away

        return {
            "home": raw_home / total,
            "draw": raw_draw / total,
            "away": raw_away / total,
        }

    def expected_value(self, calibrated_prob: float, odds: float) -> float:
        """计算期望值: EV = calibrated_prob × odds - 1。"""
        if odds <= 1.0:
            return -1.0
        return calibrated_prob * odds - 1.0

    def min_profitable_edge(
        self,
        overround_pct: float,
        safety_margin: float = 0.02,
    ) -> float:
        """
        计算最小盈利边际。

        只有边际 > 抽水率 + 安全垫 时, 投注才有正期望。
        """
        return overround_pct + safety_margin

    def compute(
        self,
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        calibrated_probs: Dict[str, float],
        is_jingcai: bool = False,
    ) -> MatchEdgeResult:
        """
        计算整场比赛的边际。

        Args:
            odds_home/draw/away: 赔率
            calibrated_probs: 校准后的模型概率
            is_jingcai: 是否竞彩 (影响抽水率假设)

        Returns:
            MatchEdgeResult with per-selection edges
        """
        market = self.market_implied_probs(odds_home, odds_draw, odds_away)
        or_pct = self.overround(odds_home, odds_draw, odds_away)
        min_edge = self.min_profitable_edge(or_pct, self.SAFETY_MARGIN)

        odds_map = {"home": odds_home, "draw": odds_draw, "away": odds_away}
        edges: Dict[str, EdgeResult] = {}
        best_sel: Optional[str] = None
        best_edge = -1.0

        for sel in ["home", "draw", "away"]:
            cal_p = calibrated_probs.get(sel, 0)
            mkt_p = market.get(sel, 0)
            o = odds_map[sel]
            edge = cal_p - mkt_p
            ev = self.expected_value(cal_p, o)
            is_value = edge > 0 and ev > 0

            edges[sel] = EdgeResult(
                selection=sel,
                calibrated_prob=cal_p,
                market_prob=mkt_p,
                edge=edge,
                ev=ev,
                odds=o,
                is_value=is_value,
            )

            if is_value and edge > best_edge:
                best_edge = edge
                best_sel = sel

        return MatchEdgeResult(
            edges=edges,
            overround_pct=or_pct,
            min_profitable_edge=min_edge,
            best_selection=best_sel,
        )

    def compute_single(
        self,
        odds: float,
        calibrated_prob: float,
    ) -> EdgeResult:
        """计算单个选项的边际 (不涉及整场比赛的归一化)。"""
        if odds <= 1.0:
            market_p = 0.5
        else:
            market_p = 1.0 / odds

        edge = calibrated_prob - market_p
        ev = self.expected_value(calibrated_prob, odds)
        is_value = edge > 0 and ev > 0

        return EdgeResult(
            selection="single",
            calibrated_prob=calibrated_prob,
            market_prob=market_p,
            edge=edge,
            ev=ev,
            odds=odds,
            is_value=is_value,
        )
