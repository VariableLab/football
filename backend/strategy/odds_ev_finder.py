"""
赔率驱动 EV 计算器 — 针对每种竞彩玩法计算最优下注策略。

核心流程:
1. 模型输出概率分布 (SPF/RQ/Score/Goals/Half)
2. 从赔率反推市场隐含概率
3. 计算每种玩法的 Edge = 模型概率 - 市场隐含概率
4. 计算 EV = 模型概率 × 赔率 - 1
5. 筛选 EV > 0 的选项
6. 按 EV 排序，给出最优策略

关键创新:
- 不只算 SPF，而是遍历所有玩法 (SPF/RQ/Score/Goals/Half)
- 对 Score/Goals/Half 使用参考赔率估算
- 考虑竞彩 89% 返水率 (抽水 11%)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import numpy as np


@dataclass
class PickCandidate:
    """单个候选下注选项"""
    play_type: str           # SPF / RQ / SCORE / GOALS / HALF
    selection: str           # "home" / "1:0" / "2球" / "主主"
    selection_label: str     # 人类可读标签
    model_prob: float        # 模型概率
    market_prob: float       # 市场隐含概率
    odds: float              # 赔率
    edge: float              # model_prob - market_prob
    ev: float                # model_prob * odds - 1
    is_value: bool           # edge > 0 AND ev > 0
    confidence: str          # high / medium / low
    kelly_fraction: float    # Kelly 比例
    risk_score: float        # 0-100


@dataclass
class EVReport:
    """整场比赛的 EV 报告"""
    match_id: int
    candidates: List[PickCandidate] = field(default_factory=list)
    best_spf: Optional[PickCandidate] = None
    best_rq: Optional[PickCandidate] = None
    best_score: Optional[PickCandidate] = None
    best_goals: Optional[PickCandidate] = None
    best_half: Optional[PickCandidate] = None
    total_value_bets: int = 0
    max_ev: float = 0.0

    @property
    def recommended_picks(self) -> List[PickCandidate]:
        """返回所有推荐的下注选项 (按 EV 排序)"""
        return sorted(
            [c for c in self.candidates if c.is_value],
            key=lambda c: c.ev,
            reverse=True,
        )


class OddsEVFinder:
    """
    赔率驱动 EV 计算器。

    用法:
        finder = OddsEVFinder()
        report = finder.find(
            match_id=1,
            spf_probs={"home": 0.55, "draw": 0.25, "away": 0.20},
            rq_probs={"home": 0.40, "draw": 0.35, "away": 0.25, "handicap": -1},
            score_probs={"1:0": 0.15, "2:1": 0.12, ...},
            goals_probs={"2": 0.25, "1": 0.20, ...},
            half_probs={"主主": 0.40, ...},
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            jingcai=True,
        )
    """

    # 竞彩官方返水率 (约 89%)
    JINGCAI_RETENTION = 0.89
    # 欧洲主流返水率 (约 95%)
    EUROPEAN_RETENTION = 0.95

    # 玩法参考赔率 (用于 Score/Goals/Half)
    SCORE_REFERENCE_ODDS = {
        "0:0": 8.0, "1:0": 6.0, "0:1": 7.0, "1:1": 6.5,
        "2:0": 7.5, "0:2": 9.0, "2:1": 7.5, "1:2": 8.5,
        "2:2": 13.0, "3:0": 11.0, "0:3": 16.0, "3:1": 10.0,
        "1:3": 13.0, "3:2": 14.0, "2:3": 16.0,
        "4:0": 18.0, "0:4": 28.0, "4:1": 15.0,
    }
    GOALS_REFERENCE_ODDS = {
        "0": 12.0, "1": 5.5, "2": 3.8, "3": 3.6,
        "4": 4.5, "5": 6.5, "6": 10.0, "7+": 12.0,
    }
    HALF_REFERENCE_ODDS = {
        "主主": 3.0, "主平": 13.0, "主客": 35.0,
        "平主": 5.0, "平平": 5.5, "平客": 11.0,
        "客主": 25.0, "客平": 13.0, "客客": 4.0,
    }

    # 标签映射
    LABELS = {
        "SPF": {"home": "主胜", "draw": "平局", "away": "客胜"},
        "RQ": {"home": "让球主胜", "draw": "让球平", "away": "让球客胜"},
        "GOALS": {"0": "0球", "1": "1球", "2": "2球", "3": "3球",
                  "4": "4球", "5": "5球", "6": "6球", "7+": "7+球"},
        "HALF": {},  # 直接使用键名
    }

    def __init__(
        self,
        is_jingcai: bool = True,
        safety_margin: float = 0.02,
        min_ev_threshold: float = 0.01,
    ):
        self.retention = self.JINGCAI_RETENTION if is_jingcai else self.EUROPEAN_RETENTION
        self.safety_margin = safety_margin
        self.min_ev_threshold = min_ev_threshold

    def find(
        self,
        match_id: int,
        spf_probs: Dict[str, float],
        rq_probs: Optional[Dict[str, float]] = None,
        score_probs: Optional[Dict[str, float]] = None,
        goals_probs: Optional[Dict[str, float]] = None,
        half_probs: Optional[Dict[str, float]] = None,
        odds_home: float = 2.0,
        odds_draw: float = 3.5,
        odds_away: float = 3.5,
        handicap: int = 0,
    ) -> EVReport:
        """
        计算所有玩法的 EV 报告。

        返回 EVReport，包含:
        - 所有候选选项
        - 每种玩法的最佳选项
        - 推荐下注列表 (按 EV 排序)
        """
        report = EVReport(match_id=match_id)

        # 1. SPF
        spf_candidates = self._evaluate_spf(spf_probs, odds_home, odds_draw, odds_away)
        report.candidates.extend(spf_candidates)
        if spf_candidates:
            report.best_spf = max(spf_candidates, key=lambda c: c.ev)

        # 2. RQ
        if rq_probs:
            rq_candidates = self._evaluate_rq(rq_probs, odds_home, odds_draw, odds_away, handicap)
            report.candidates.extend(rq_candidates)
            if rq_candidates:
                report.best_rq = max(rq_candidates, key=lambda c: c.ev)

        # 3. Score
        if score_probs:
            score_candidates = self._evaluate_exotic(
                score_probs, "SCORE", self.SCORE_REFERENCE_ODDS
            )
            report.candidates.extend(score_candidates)
            if score_candidates:
                report.best_score = max(score_candidates, key=lambda c: c.ev)

        # 4. Goals
        if goals_probs:
            goals_candidates = self._evaluate_exotic(
                goals_probs, "GOALS", self.GOALS_REFERENCE_ODDS
            )
            report.candidates.extend(goals_candidates)
            if goals_candidates:
                report.best_goals = max(goals_candidates, key=lambda c: c.ev)

        # 5. Half
        if half_probs:
            half_candidates = self._evaluate_exotic(
                half_probs, "HALF", self.HALF_REFERENCE_ODDS
            )
            report.candidates.extend(half_candidates)
            if half_candidates:
                report.best_half = max(half_candidates, key=lambda c: c.ev)

        # 统计
        report.total_value_bets = sum(1 for c in report.candidates if c.is_value)
        report.max_ev = max((c.ev for c in report.candidates), default=0.0)

        return report

    def _evaluate_spf(
        self,
        probs: Dict[str, float],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
    ) -> List[PickCandidate]:
        """评估 SPF 玩法"""
        candidates = []
        odds_map = {"home": odds_home, "draw": odds_draw, "away": odds_away}

        for sel, p in probs.items():
            o = odds_map.get(sel, 0)
            if o <= 1.01:
                continue

            market_p = 1.0 / o
            edge = p - market_p
            ev = p * o - 1.0

            # 考虑返水率
            adjusted_ev = ev * self.retention

            candidates.append(PickCandidate(
                play_type="SPF",
                selection=sel,
                selection_label=self._label("SPF", sel),
                model_prob=round(p, 4),
                market_prob=round(market_p, 4),
                odds=round(o, 2),
                edge=round(edge, 4),
                ev=round(adjusted_ev, 4),
                is_value=edge > 0 and adjusted_ev > self.min_ev_threshold,
                confidence=self._confidence(p, adjusted_ev),
                kelly_fraction=0.0,  # 由 PositionSizer 计算
                risk_score=0.0,
            ))

        return candidates

    def _evaluate_rq(
        self,
        probs: Dict[str, float],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        handicap: int,
    ) -> List[PickCandidate]:
        """评估 RQ 玩法"""
        candidates = []
        odds_map = {"home": odds_home, "draw": odds_draw, "away": odds_away}

        for sel, p in probs.items():
            if sel == "handicap":
                continue
            o = odds_map.get(sel, 0)
            if o <= 1.01:
                continue

            market_p = 1.0 / o
            edge = p - market_p
            ev = p * o - 1.0
            adjusted_ev = ev * self.retention

            candidates.append(PickCandidate(
                play_type="RQ",
                selection=sel,
                selection_label=self._label("RQ", sel),
                model_prob=round(p, 4),
                market_prob=round(market_p, 4),
                odds=round(o, 2),
                edge=round(edge, 4),
                ev=round(adjusted_ev, 4),
                is_value=edge > 0 and adjusted_ev > self.min_ev_threshold,
                confidence=self._confidence(p, adjusted_ev),
                kelly_fraction=0.0,
                risk_score=0.0,
            ))

        return candidates

    def _evaluate_exotic(
        self,
        probs: Dict[str, float],
        play_type: str,
        ref_odds: Dict[str, float],
    ) -> List[PickCandidate]:
        """评估 exotic 玩法 (Score/Goals/Half)"""
        candidates = []

        for sel, p in probs.items():
            # 使用参考赔率估算
            o = ref_odds.get(sel, self._estimate_odds_from_prob(p))
            if o <= 1.01:
                continue

            market_p = 1.0 / o
            edge = p - market_p
            ev = p * o - 1.0
            adjusted_ev = ev * self.retention

            candidates.append(PickCandidate(
                play_type=play_type,
                selection=sel,
                selection_label=self._label(play_type, sel),
                model_prob=round(p, 4),
                market_prob=round(market_p, 4),
                odds=round(o, 2),
                edge=round(edge, 4),
                ev=round(adjusted_ev, 4),
                is_value=edge > 0 and adjusted_ev > self.min_ev_threshold,
                confidence=self._confidence(p, adjusted_ev),
                kelly_fraction=0.0,
                risk_score=0.0,
            ))

        return candidates

    def _estimate_odds_from_prob(self, prob: float) -> float:
        """根据概率估算参考赔率 (保守估计)"""
        if prob <= 0:
            return 99.0
        # 返还率 89%
        return 1.0 / (prob * self.retention)

    def _label(self, play_type: str, key: str) -> str:
        """获取人类可读标签"""
        labels = self.LABELS.get(play_type, {})
        return labels.get(key, key)

    @staticmethod
    def _confidence(prob: float, ev: float) -> str:
        """置信度评估"""
        if prob >= 0.55 and ev >= 0.05:
            return "high"
        elif prob >= 0.45 and ev >= 0.02:
            return "medium"
        return "low"

    def generate_report_text(self, report: EVReport) -> str:
        """生成可读的 EV 报告文本"""
        lines = [f"=== EV Report for Match #{report.match_id} ==="]
        lines.append(f"Total candidates: {len(report.candidates)}")
        lines.append(f"Value bets found: {report.total_value_bets}")
        lines.append(f"Max EV: {report.max_ev:.4f}")
        lines.append("")

        picks = report.recommended_picks
        if not picks:
            lines.append("No value bets found. Market is efficient.")
            return "\n".join(lines)

        lines.append("Recommended Picks (sorted by EV):")
        lines.append("-" * 60)
        for i, pick in enumerate(picks[:10], 1):
            lines.append(
                f"  {i}. [{pick.play_type}] {pick.selection_label} "
                f"@ {pick.odds:.2f} | Model: {pick.model_prob:.1%} "
                f"| Market: {pick.market_prob:.1%} | Edge: {pick.edge:+.4f} | "
                f"EV: {pick.ev:+.4f} | Conf: {pick.confidence}"
            )

        return "\n".join(lines)
