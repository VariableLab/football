"""
期望收益最大化对冲投注策略 (EV Maximizing & Defensive Hedging Strategy)

核心思想：
1. 识别赔率中具备正期望值 (Positive Expected Value, EV) 且赔率较高的冷门选项（如高赔客胜、平局、高赔比分等）作为主攻。
2. 匹配防御性极高（低赔、高概率）的项进行 Dutching 对冲，确保防御项打出时本金完全无损 (保本)。
3. 使用凯利公式 (Fractional Kelly) 计算最优资金仓位配比，保证收益最大化的同时控制回撤风险。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from strategy.hedged_portfolio import PortfolioLeg, PortfolioRecommendation
from strategy.strategy_pipeline import SCORE_REFERENCE_ODDS


class EVMaximizingStrategy:
    """
    EV 最大化博冷对冲策略引擎
    """
    MAX_TOTAL_STAKE = 0.20  # 单场最大投入比例限制 (20% 资金)
    KELLY_FRACTION = 0.15   # 0.15倍 Fractional Kelly，提供极高的防守缓冲

    def __init__(
        self,
        match_predictions: List[Dict[str, Any]],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        collapse_prob: float = 0.0,
    ):
        self.preds = match_predictions
        self.odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}
        self.collapse_prob = collapse_prob

    def _get_play_probs(self, play_type: str) -> Dict[str, float]:
        for p in self.preds:
            if p.get("play_type") == play_type:
                return p.get("probabilities", {})
        return {}

    def _fractional_kelly(self, ev: float, odds: float) -> float:
        """计算 15% 凯利比例仓位"""
        if ev <= 0 or odds <= 1.0:
            return 0.0
        # b = odds - 1.0
        # f = (p * b - q) / b = ev / (odds - 1)
        raw_k = ev / (odds - 1.0)
        return min(round(raw_k * self.KELLY_FRACTION, 4), self.MAX_TOTAL_STAKE)

    def generate(self, min_ev: float = 0.04) -> List[PortfolioRecommendation]:
        """生成期望收益最大化对冲组合"""
        recommendations = []

        spf_probs = self._get_play_probs("SPF")
        score_probs = self._get_play_probs("SCORE")

        if not spf_probs:
            return []

        # ──────────────────────────────────────────────────────────
        # 1. 胜平负高赔正期望对冲策略 (Positive EV Underdog Hedge)
        # ──────────────────────────────────────────────────────────
        # 遍历胜平负选项，寻找高期望 (EV > min_ev) 且高赔 (Odds >= 3.0) 的爆冷候选项
        for outcome, prob in spf_probs.items():
            odds_val = self.odds.get(outcome, 1.0)
            if odds_val <= 1.0:
                continue

            ev_val = prob * odds_val - 1.0
            # 筛选爆冷高赔正期望项
            if ev_val > min_ev and odds_val >= 3.0:
                # 寻找防守对冲项（赔率较低、概率较高的反向结果）
                # 比如：预测平局（高赔）或客胜（爆冷高赔）打出，对冲强队主胜
                others = [k for k in ["home", "draw", "away"] if k != outcome]
                # 选概率最高的那个作为对冲防御
                hedge_key = max(others, key=lambda k: spf_probs.get(k, 0.0))
                h_prob = spf_probs.get(hedge_key, 0.0)
                h_odds = self.odds.get(hedge_key, 1.0)

                if h_odds <= 1.1:
                    continue

                # Dutching 保本仓位计算: s_hedge * h_odds = 1.0 (确保打出对冲项收回 100% 本金)
                s_hedge = 1.0 / h_odds
                s_primary = 1.0 - s_hedge

                # 验证主攻占比是否合理（对冲比例不能过高，否则无获利空间）
                if s_primary <= 0.10:
                    continue

                # 整体投资组合期望回报率
                combined_roi = (prob * s_primary * odds_val) + (h_prob * s_hedge * h_odds) - 1.0

                if combined_roi > min_ev:
                    kelly = self._fractional_kelly(combined_roi, odds_val)
                    recommendations.append(PortfolioRecommendation(
                        strategy_type="PositiveEVUnderdogHedge",
                        name=f"冷门博弈 {outcome.upper()} + {hedge_key.upper()}保本对冲",
                        legs=[
                            PortfolioLeg("primary", "SPF", outcome, odds_val, prob, round(s_primary, 4), ev_val),
                            PortfolioLeg("hedge", "SPF", hedge_key, h_odds, h_prob, round(s_hedge, 4), 0.0),
                        ],
                        expected_roi=round(combined_roi, 4),
                        win_prob_combined=round(prob + h_prob, 4),
                        total_ev=round(combined_roi, 4),
                        kelly_fraction=kelly,
                        rationale=(
                            f"捕获正期望冷门 {outcome.upper()} (期望值 {ev_val*100:+.1f}%, 赔率 {odds_val:.2f})，"
                            f"分配 {s_primary*100:.1f}% 仓位主攻；匹配 {hedge_key.upper()} 赔率 {h_odds:.2f} 进行保本对冲，"
                            f"保本仓位 {s_hedge*100:.1f}%。组合整体期望 ROI 为 {combined_roi*100:.1f}%。"
                        )
                    ))

        # ──────────────────────────────────────────────────────────
        # 2. 比分爆冷正期望对冲策略 (Positive EV Score Hedge)
        # ──────────────────────────────────────────────────────────
        if score_probs:
            sorted_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)
            for score, prob in sorted_scores[:6]:  # 筛选前 6 个概率最高的比分
                o_ref = SCORE_REFERENCE_ODDS.get(score, 10.0)
                ev_primary = prob * o_ref - 1.0

                # 筛选比分中收益率高且赔率高 (Odds >= 6.0) 的黄金博冷项
                if ev_primary > min_ev and o_ref >= 6.0:
                    parts = score.split(":")
                    h_goals, a_goals = int(parts[0]), int(parts[1])

                    # 战术性选择对冲：
                    # 主胜比分对冲不败/客胜；客胜比分对冲不败/主胜
                    if h_goals > a_goals:
                        hedge_key = "draw" if self.odds.get("draw", 3.0) < self.odds.get("away", 3.0) else "away"
                    else:
                        hedge_key = "draw" if self.odds.get("draw", 3.0) < self.odds.get("home", 3.0) else "home"

                    h_odds = self.odds.get(hedge_key, 3.0)
                    h_prob = spf_probs.get(hedge_key, 0.0)

                    if h_odds <= 1.1:
                        continue

                    # Dutching
                    s_hedge = 1.0 / h_odds
                    s_primary = 1.0 - s_hedge

                    if s_primary <= 0.15:
                        continue

                    combined_roi = (prob * s_primary * o_ref) + (h_prob * s_hedge * h_odds) - 1.0

                    if combined_roi > min_ev:
                        kelly = self._fractional_kelly(combined_roi, o_ref)
                        recommendations.append(PortfolioRecommendation(
                            strategy_type="PositiveEVScoreHedge",
                            name=f"比分博冷 {score} + {hedge_key.upper()}对冲防守",
                            legs=[
                                PortfolioLeg("primary", "SCORE", score, o_ref, prob, round(s_primary, 4), ev_primary),
                                PortfolioLeg("hedge", "SPF", hedge_key, h_odds, h_prob, round(s_hedge, 4), 0.0),
                            ],
                            expected_roi=round(combined_roi, 4),
                            win_prob_combined=round(prob + h_prob, 4),
                            total_ev=round(combined_roi, 4),
                            kelly_fraction=kelly,
                            rationale=(
                                f"模型预测比分 {score} 概率 {prob:.1%} 具备极高正期望（参考赔率 {o_ref:.1f}），"
                                f"分派 {s_primary*100:.1f}% 仓位。匹配胜平负 {hedge_key.upper()} 赔率 {h_odds:.2f} 对冲保本，"
                                f"整体期望 ROI 达 {combined_roi*100:.1f}%。"
                            )
                        ))

        # 按期望 ROI 排序，返回最优秀的前 3 项推荐
        recommendations.sort(key=lambda p: p.expected_roi, reverse=True)
        return recommendations[:3]
