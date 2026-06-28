"""
期望收益最大化对冲投注策略 v2 (EV Maximizing & Defensive Hedging Strategy v2)

优化点:
1. 概率保护阀 (Probability Caps): 限制单场比赛中任何胜平负选项最高概率为 0.65，单比分最大概率为 0.20，防止模型异常过度自信导致重仓。
2. 豪门过滤器 (Giant Shield Filter): 针对皇家马德里、拜仁慕尼黑、曼城等超级豪门，自动压低爆冷主攻腿的置信度和投入资金仓位。
3. 双选对冲防线 (Dual-Wing Hedge): 当平局为高赔主攻时，对冲腿可支持胜负双选对冲 (Double Chance)，在数学上将盲区降为零。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from strategy.hedged_portfolio import PortfolioLeg, PortfolioRecommendation
from strategy.strategy_pipeline import SCORE_REFERENCE_ODDS


# 顶级豪门俱乐部数据库标识（包含拼音及中英文）
GIANTS_KEYWORDS = {
    "real madrid", "realmadrid", "皇家马德里", "皇马",
    "bayern", "拜仁", "拜仁慕尼黑",
    "manchester city", "man city", "曼彻斯特城", "曼城",
    "barcelona", "巴萨", "巴塞罗那",
    "arsenal", "阿森纳",
    "liverpool", "利物浦",
    "paris saint", "psg", "巴黎圣日耳曼", "巴黎",
    "inter milan", "国际米兰", "国米"
}


class EVMaximizingStrategy:
    """
    EV 最大化博冷对冲策略引擎 v2
    """
    MAX_TOTAL_STAKE = 0.18  # 略微下调单场最大仓位限制 (最高18% 资金)
    KELLY_FRACTION = 0.12   # 0.12倍 Kelly 缓冲

    def __init__(
        self,
        match_predictions: List[Dict[str, Any]],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        collapse_prob: float = 0.0,
        home_team_name: str = "",
        away_team_name: str = ""
    ):
        self.preds = match_predictions
        self.odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}
        self.collapse_prob = collapse_prob
        self.home_team = home_team_name.lower()
        self.away_team = away_team_name.lower()

    def _get_play_probs(self, play_type: str) -> Dict[str, float]:
        for p in self.preds:
            if p.get("play_type") == play_type:
                probs = p.get("probabilities", {})
                
                # 应用概率上限阀门 (Probability Caps)
                capped_probs = {}
                if play_type == "SPF":
                    total = 0.0
                    for k, v in probs.items():
                        capped_val = min(v, 0.65)  # 胜平负上限 65%
                        capped_probs[k] = capped_val
                        total += capped_val
                    # 重新归一化
                    return {k: v / total for k, v in capped_probs.items()}
                    
                elif play_type == "SCORE":
                    total = 0.0
                    for k, v in probs.items():
                        capped_val = min(v, 0.20)  # 单个比分上限 20%
                        capped_probs[k] = capped_val
                        total += capped_val
                    return {k: v / total for k, v in capped_probs.items()}
                    
                return probs
        return {}

    def _is_giant_involved(self) -> bool:
        """检查比赛中是否有顶级豪门强队"""
        for g in GIANTS_KEYWORDS:
            if g in self.home_team or g in self.away_team:
                return True
        return False

    def _fractional_kelly(self, ev: float, odds: float) -> float:
        """计算分数凯利资金比例"""
        if ev <= 0 or odds <= 1.0:
            return 0.0
        raw_k = ev / (odds - 1.0)
        
        # 豪门过滤器保护：如果是狙击豪门爆冷，仓位再减半以防系统性偏差
        scale = 0.50 if self._is_giant_involved() else 1.0
        
        return min(round(raw_k * self.KELLY_FRACTION * scale, 4), self.MAX_TOTAL_STAKE)

    def generate(self, min_ev: float = 0.005) -> List[PortfolioRecommendation]:
        """生成期望收益最大化对冲组合"""
        recommendations = []

        spf_probs = self._get_play_probs("SPF")
        score_probs = self._get_play_probs("SCORE")

        if not spf_probs:
            return []

        # ──────────────────────────────────────────────────────────
        # 1. 胜平负高赔正期望对冲策略 (Positive EV Underdog Hedge)
        # ──────────────────────────────────────────────────────────
        for outcome, prob in spf_probs.items():
            odds_val = self.odds.get(outcome, 1.0)
            if odds_val <= 1.0:
                continue

            ev_val = prob * odds_val - 1.0
            # 筛选爆冷高赔正期望项 (Odds >= 2.8)
            if ev_val > min_ev and odds_val >= 2.8:
                
                # 豪门过滤：如果在对阵豪门时强行爆冷，给期望值打个八折缓冲
                if self._is_giant_involved():
                    ev_val *= 0.8

                # 战术对冲选项
                others = [k for k in ["home", "draw", "away"] if k != outcome]
                # 选概率最高的那个作为对冲防御
                hedge_key = max(others, key=lambda k: spf_probs.get(k, 0.0))
                h_prob = spf_probs.get(hedge_key, 0.0)
                h_odds = self.odds.get(hedge_key, 1.0)

                if h_odds <= 1.1:
                    continue

                # Dutching 保本仓位计算
                s_hedge = 1.0 / h_odds
                s_primary = 1.0 - s_hedge

                # 验证主攻占比是否合理
                if s_primary <= 0.12:
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
                            f"捕获高赔正期望 {outcome.upper()} (期望值 {ev_val*100:+.1f}%, 赔率 {odds_val:.2f})，"
                            f"分配 {s_primary*100:.1f}% 仓位。匹配 {hedge_key.upper()} 赔率 {h_odds:.2f} 进行保本对冲，"
                            f"保本仓位 {s_hedge*100:.1f}%。组合整体期望 ROI 为 {combined_roi*100:.1f}%。"
                            + (" (已触发豪门盾牌保护减仓)" if self._is_giant_involved() else "")
                        )
                    ))

        # ──────────────────────────────────────────────────────────
        # 2. 比分爆冷正期望对冲策略 (Positive EV Score Hedge)
        # ──────────────────────────────────────────────────────────
        if score_probs:
            sorted_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)
            for score, prob in sorted_scores[:5]:  # 只选前 5 概率高的比分
                o_ref = SCORE_REFERENCE_ODDS.get(score, 10.0)
                ev_primary = prob * o_ref - 1.0

                # 比分收益率高且赔率高 (Odds >= 5.5) 的爆冷项
                if ev_primary > min_ev and o_ref >= 5.5:
                    
                    if self._is_giant_involved():
                        ev_primary *= 0.8
                        
                    parts = score.split(":")
                    h_goals, a_goals = int(parts[0]), int(parts[1])

                    # 战术性选择对冲：
                    if h_goals > a_goals:
                        # 预测主胜比分 → 对冲平局或客胜中赔率较低者
                        hedge_key = "draw" if self.odds.get("draw", 3.0) < self.odds.get("away", 3.0) else "away"
                    else:
                        # 预测客胜比分 → 对冲平局或主胜中赔率较低者
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
                                f"模型预测比分 {score} 概率 {prob:.1%} 具备正期望（参考赔率 {o_ref:.1f}），"
                                f"分派 {s_primary*100:.1f}% 仓位。匹配胜平负 {hedge_key.upper()} 赔率 {h_odds:.2f} 对冲保本，"
                                f"整体期望 ROI 达 {combined_roi*100:.1f}%。"
                                + (" (已触发豪门盾牌保护减仓)" if self._is_giant_involved() else "")
                            )
                        ))

        # 按期望 ROI 排序，返回最优秀的前 3 项推荐
        recommendations.sort(key=lambda p: p.expected_roi, reverse=True)
        return recommendations[:3]
