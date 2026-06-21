"""
对冲投资组合生成器 (Hedged Portfolio Generator)

核心思路:
- 用高赔率的"主攻"选项博取超额收益
- 用低赔率的"对冲"选项保护本金
- 通过 Fractional Kelly 优化仓位分配

策略类型:
1. Score + SPF Hedge: 比分主攻 + 胜平负防守
2. Double Score + Draw Hedge: 双选比分 + 平局保本
3. HT/FT + Opposite Hedge: 半全场主攻 + 反向对冲
4. Collapsible Upset Play: 崩盘信号触发的大比分狙击
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from strategy.strategy_pipeline import SCORE_REFERENCE_ODDS


@dataclass
class PortfolioLeg:
    """组合中的一个腿 (单一下注项)"""
    leg_type: str          # "primary" or "hedge"
    play: str              # "SCORE", "SPF", "HALF", etc.
    selection: str         # "1:0", "home", "主主", etc.
    odds: float            # 赔率
    probability: float     # 模型概率
    stake_pct: float       # 仓位占比 (所有腿加起来 = 1.0)
    ev: float = 0.0        # 期望价值


@dataclass
class PortfolioRecommendation:
    """完整的对冲投资组合推荐"""
    strategy_type: str
    name: str
    legs: List[PortfolioLeg] = field(default_factory=list)
    expected_roi: float = 0.0
    win_prob_combined: float = 0.0
    total_ev: float = 0.0
    kelly_fraction: float = 0.0
    rationale: str = ""
    collapse_prob: float = 0.0  # 崩盘概率信号


class HedgedPortfolioGenerator:
    """
    跨玩法对冲投资组合生成器。

    用法:
        gen = HedgedPortfolioGenerator(
            match_predictions=pred_list,
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
        )
        portfolios = gen.generate(min_ev=0.03)
    """

    # 最大单次总仓位 (Fractional Kelly = 1/4)
    MAX_TOTAL_STAKE = 0.25
    KELLY_FRACTION = 0.25

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

    def generate(self, min_ev: float = 0.03) -> List[PortfolioRecommendation]:
        """生成所有可用的对冲投资组合"""
        portfolios = []
        portfolios.extend(self._score_plus_spf_hedge(min_ev))
        portfolios.extend(self._double_score_draw_hedge(min_ev))
        portfolios.extend(self._htft_opposite_hedge(min_ev))
        portfolios.extend(self._collapse_big_score_sniper(min_ev))
        portfolios.extend(self._market_divergence_play(min_ev))

        # 按 EV 排序
        portfolios.sort(key=lambda p: p.expected_roi, reverse=True)
        return portfolios[:5]  # 最多返回 5 个

    # ─────────────────────────────────────
    # 策略 1: 比分 + SPF 对冲
    # ─────────────────────────────────────
    def _score_plus_spf_hedge(self, min_ev: float) -> List[PortfolioRecommendation]:
        """单比分 + SPF 对冲"""
        score_probs = self._get_play_probs("SCORE")
        spf_probs = self._get_play_probs("SPF")
        results = []

        # 按概率排序比分
        sorted_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)

        for score, prob in sorted_scores[:8]:  # 只看 Top 8
            o_ref = SCORE_REFERENCE_ODDS.get(score, 10.0)
            ev_primary = prob * o_ref - 1.0

            if ev_primary < min_ev:
                continue

            # 计算对冲腿
            parts = score.split(":")
            home_goals, away_goals = int(parts[0]), int(parts[1])

            # 如果预测主胜比分 → 对冲选"不让球客胜"或"平局"
            if home_goals > away_goals:
                # 主胜比分 → 对冲平局
                hedge_sel, hedge_key = "draw", "draw"
            else:
                # 客胜比分 → 对冲平局
                hedge_sel, hedge_key = "draw", "draw"

            h_odds = self.odds.get(hedge_key, 3.5)
            h_prob = spf_probs.get(hedge_key, 0.0)
            if h_odds <= 1.01:
                continue

            # Dutching 数学: S_hedge = 1 / h_odds (保证对冲腿收回全部投入)
            s_hedge = 1.0 / h_odds
            s_primary = 1.0 - s_hedge

            if s_primary <= 0 or s_hedge >= 0.95:
                continue  # 对冲成本太高

            # 预期回报
            exp_return = prob * (s_primary * o_ref) + h_prob * (s_hedge * h_odds)
            exp_roi = exp_return - 1.0

            if exp_roi > min_ev:
                kelly = self._fractional_kelly(ev_primary, o_ref)
                results.append(PortfolioRecommendation(
                    strategy_type="ScoreSPFHedge",
                    name=f"比分狙击 {score} + 平局保本",
                    legs=[
                        PortfolioLeg("primary", "SCORE", score, o_ref, prob, s_primary, ev_primary),
                        PortfolioLeg("hedge", "SPF", hedge_sel, h_odds, h_prob, s_hedge, 0.0),
                    ],
                    expected_roi=round(exp_roi, 4),
                    win_prob_combined=round(prob + h_prob, 4),
                    total_ev=round(prob * s_primary * o_ref + h_prob * s_hedge * h_odds - 1, 4),
                    kelly_fraction=kelly,
                    rationale=(
                        f"主攻比分 {score} (模型概率{prob:.0%}, 参考赔率{o_ref:.1f}) "
                        f"对冲平局 (赔率{h_odds:.1f}) 保本。"
                        f"预期 ROI {exp_roi*100:.1f}%。"
                    ),
                ))

        return results

    # ─────────────────────────────────────
    # 策略 2: 双选比分 + 平局对冲
    # ─────────────────────────────────────
    def _double_score_draw_hedge(self, min_ev: float) -> List[PortfolioRecommendation]:
        """双选比分 + 平局对冲"""
        score_probs = self._get_play_probs("SCORE")
        spf_probs = self._get_play_probs("SPF")
        results = []

        home_scores = ["1:0", "2:0", "2:1", "3:0", "3:1", "4:0", "4:1"]
        away_scores = ["0:1", "0:2", "1:2", "0:3", "1:3", "0:4", "1:4"]

        # 选 Top 2 主胜比分
        home_sp = {s: score_probs.get(s, 0) for s in home_scores}
        top_home = sorted(home_sp.items(), key=lambda x: x[1], reverse=True)[:2]

        # 选 Top 2 客胜比分
        away_sp = {s: score_probs.get(s, 0) for s in away_scores}
        top_away = sorted(away_sp.items(), key=lambda x: x[1], reverse=True)[:2]

        for direction, top_scores in [("home", top_home), ("away", top_away)]:
            combined_prob = sum(p for s, p in top_scores)
            if combined_prob < 0.10:
                continue

            # 对冲: 平局
            h_odds = self.odds.get("draw", 3.5)
            h_prob = spf_probs.get("draw", 0.0)
            s_hedge = 1.0 / h_odds if h_odds > 1.01 else 0.9
            if s_hedge >= 0.85:
                continue

            R = 1.0 - s_hedge  # 留给主攻的预算
            s_hedge = round(s_hedge, 4)

            # Dutching: 按 1/odds 比例分配 R
            odds_list = [SCORE_REFERENCE_ODDS.get(s, 8.0) for s, _ in top_scores]
            inv_odds = [1.0 / max(o, 1.01) for o in odds_list]
            sum_inv = sum(inv_odds)

            legs = []
            total_ev = 0.0
            for (sc, pr), inv_o, o in zip(top_scores, inv_odds, odds_list):
                stake = R * (inv_o / sum_inv) if sum_inv > 0 else R / 2
                ev = pr * o - 1.0
                legs.append(PortfolioLeg("primary", "SCORE", sc, o, pr, round(stake, 4), ev))
                total_ev += pr * stake * o

            total_ev += h_prob * s_hedge * h_odds  # 对冲腿回报
            total_ev -= 1.0  # 总投入 = 1.0

            if total_ev > min_ev:
                names = {s: f"{sc}" for s, _ in top_scores}
                score_str = " & ".join(names.values())
                results.append(PortfolioRecommendation(
                    strategy_type="DoubleScoreDrawHedge",
                    name=f"双选比分 + 平局保本 ({score_str})",
                    legs=legs + [
                        PortfolioLeg("hedge", "SPF", "draw", h_odds, h_prob, s_hedge, 0.0),
                    ],
                    expected_roi=round(total_ev, 4),
                    win_prob_combined=round(combined_prob + h_prob, 4),
                    total_ev=round(total_ev, 4),
                    kelly_fraction=self._fractional_kelly(total_ev, 2.0),
                    rationale=(
                        f"主攻比分 {score_str} (合计概率{combined_prob:.0%}) "
                        f"平局对冲保本。预期 ROI {total_ev*100:.1f}%。"
                    ),
                ))

        return results

    # ─────────────────────────────────────
    # 策略 3: 半全场 + 反向对冲
    # ─────────────────────────────────────
    def _htft_opposite_hedge(self, min_ev: float) -> List[PortfolioRecommendation]:
        """半全场高赔 + 胜平负对冲"""
        half_probs = self._get_play_probs("HALF")
        spf_probs = self._get_play_probs("SPF")
        results = []

        for htft, prob in sorted(half_probs.items(), key=lambda x: x[1], reverse=True):
            if prob < 0.06:
                continue

            # 参考赔率
            ref_odds = self._get_htft_odds(htft)
            if ref_odds <= 1.01:
                continue

            ev = prob * ref_odds - 1.0
            if ev < min_ev:
                continue

            # 对冲: 选择与 HT/FT 最不可能打出的 SPF 选项
            # 例如 "平主" → 对冲 "客胜"
            hedge_map = {
                "主主": "away",
                "主平": "away",
                "主客": "home",
                "平主": "away",
                "平平": "home",
                "平客": "home",
                "客主": "draw",
                "客平": "draw",
                "客客": "home",
            }
            hedge_key = hedge_map.get(htft, "draw")
            h_odds = self.odds.get(hedge_key, 3.5)
            h_prob = spf_probs.get(hedge_key, 0.0)

            if h_odds <= 1.01:
                continue

            s_hedge = 1.0 / h_odds
            s_primary = 1.0 - s_hedge

            if s_primary <= 0:
                continue

            exp_return = prob * (s_primary * ref_odds) + h_prob * 1.0
            exp_roi = exp_return - 1.0

            if exp_roi > min_ev:
                results.append(PortfolioRecommendation(
                    strategy_type="HTFTHedge",
                    name=f"半全场 {htft} + {hedge_key}保本",
                    legs=[
                        PortfolioLeg("primary", "HALF", htft, ref_odds, prob, round(s_primary, 4), ev),
                        PortfolioLeg("hedge", "SPF", hedge_key, h_odds, h_prob, round(s_hedge, 4), 0.0),
                    ],
                    expected_roi=round(exp_roi, 4),
                    win_prob_combined=round(prob + h_prob, 4),
                    total_ev=round(exp_return - 1, 4),
                    kelly_fraction=self._fractional_kelly(ev, ref_odds),
                    rationale=f"预测半全场 {htft} (概率{prob:.0%}, 赔率{ref_odds:.1f}) 对冲{hedge_key}(赔率{h_odds:.1f})保本。",
                ))

        return results

    # ─────────────────────────────────────
    # 策略 4: 崩盘狙击 (Collapse Big Score)
    # ─────────────────────────────────────
    def _collapse_big_score_sniper(self, min_ev: float) -> List[PortfolioRecommendation]:
        """
        当 collapse_prob > 0.2 时，触发大比分狙击策略。
        瞄准 4+:0+, 3:1+, 5:1 等极端比分。
        """
        if self.collapse_prob < 0.20:
            return []

        score_probs = self._get_play_probs("SCORE")
        results = []

        # 大比分候选
        big_scores = [
            "4:0", "4:1", "5:0", "5:1", "6:0", "3:0", "3:1", "4+", "5+", "6+",
            "0:4", "0:5", "0:6", "1:4", "2:4",
        ]

        for score in big_scores:
            prob = score_probs.get(score, 0)
            if prob < 0.02:
                continue

            o_ref = SCORE_REFERENCE_ODDS.get(score, 20.0)
            ev = prob * o_ref - 1.0

            if ev < min_ev:
                continue

            # 用 SPF 的"主胜"作为对冲 (因为大比分意味着某方大胜)
            # 如果预测强队大胜 → 对冲"不让球主胜"
            if score.startswith("4") or score.startswith("5") or score.startswith("6") or "+" in score:
                # 大比分 → SPF 主胜/客胜对冲
                if ":" in score:
                    parts = score.split(":")
                    home_g, away_g = int(parts[0]) if parts[0] != "+" else 4, int(parts[1]) if len(parts) > 1 and parts[1] != "+" else 0
                    if home_g > away_g:
                        hedge_key = "away"
                    else:
                        hedge_key = "home"
                else:
                    hedge_key = "draw"

                h_odds = self.odds.get(hedge_key, 3.5)
                spf_probs_dict = self._get_play_probs("SPF")
                h_prob = spf_probs_dict.get(hedge_key, 0.0)

                if h_odds <= 1.01:
                    continue

                s_hedge = 1.0 / h_odds
                s_primary = 1.0 - s_hedge

                if s_primary <= 0 or s_hedge >= 0.9:
                    continue

                exp_return = prob * (s_primary * o_ref) + h_prob * 1.0
                exp_roi = exp_return - 1.0

                if exp_roi > min_ev:
                    results.append(PortfolioRecommendation(
                        strategy_type="CollapseSniper",
                        name=f"崩盘狙击 {score} + SPF 反向对冲",
                        legs=[
                            PortfolioLeg("primary", "SCORE", score, o_ref, prob, round(s_primary, 4), ev),
                            PortfolioLeg("hedge", "SPF", hedge_key, h_odds, h_prob, round(s_hedge, 4), 0.0),
                        ],
                        expected_roi=round(exp_roi, 4),
                        win_prob_combined=round(prob + h_prob, 4),
                        total_ev=round(exp_return - 1, 4),
                        kelly_fraction=self._fractional_kelly(ev, o_ref),
                        rationale=(
                            f"崩盘概率 {self.collapse_prob:.0%} 触发大比分狙击 {score} "
                            f"(模型概率{prob:.0%}, 赔率{o_ref:.1f})。"
                            f"对冲{hedge_key}(赔率{h_odds:.1f})保本。"
                        ),
                        collapse_prob=self.collapse_prob,
                    ))

        return results

    # ─────────────────────────────────────
    # 策略 5: 市场分歧玩法 (Market Divergence)
    # ─────────────────────────────────────
    def _market_divergence_play(self, min_ev: float) -> List[PortfolioRecommendation]:
        """
        当模型与市场分歧较大时，利用这个分歧做反向策略。
        需要导入 UpsetDetector。
        """
        try:
            from core.models.upset_detector import UpsetDetector
        except ImportError:
            return []

        spf_probs = self._get_play_probs("SPF")
        if not spf_probs:
            return []

        # 计算市场隐含概率
        market_spf = self._odds_to_market_probs(
            self.odds["home"], self.odds["draw"], self.odds["away"]
        )

        signal = UpsetDetector().detect(spf_probs, market_spf)

        if not signal.is_upset_candidate:
            return []

        # 找出分歧最大的选项
        max_diff_key = max(spf_probs, key=lambda k: abs(spf_probs[k] - market_spf.get(k, 0)))
        diff = abs(spf_probs[max_diff_key] - market_spf[max_diff_key])

        # 如果模型高估了某个选项 → 反其道而行
        if spf_probs[max_diff_key] > market_spf.get(max_diff_key, 0):
            # 模型看好 → 直接下注
            target = max_diff_key
            target_prob = spf_probs[target]
        else:
            # 模型看衰 → 不做推荐
            return []

        o_ref = self.odds.get(target, 2.5)
        if o_ref <= 1.01:
            return []

        ev = target_prob * o_ref - 1.0
        if ev < min_ev:
            return []

        # 对冲: 选 SPF 中其他两个选项中最弱的
        others = [k for k in spf_probs if k != target]
        hedge_key = min(others, key=lambda k: spf_probs.get(k, 0))
        h_odds = self.odds.get(hedge_key, 3.5)
        h_prob = spf_probs.get(hedge_key, 0.0)

        if h_odds <= 1.01:
            return []

        s_hedge = 1.0 / h_odds
        s_primary = 1.0 - s_hedge

        if s_primary <= 0:
            return []

        exp_return = target_prob * (s_primary * o_ref) + h_prob * 1.0
        exp_roi = exp_return - 1.0

        if exp_roi > min_ev:
            label_map = {"home": "主胜", "draw": "平局", "away": "客胜"}
            results = [PortfolioRecommendation(
                strategy_type="DivergencePlay",
                name=f"市场分歧 {label_map.get(target, target)} + 反向对冲",
                legs=[
                    PortfolioLeg("primary", "SPF", target, o_ref, target_prob, round(s_primary, 4), ev),
                    PortfolioLeg("hedge", "SPF", hedge_key, h_odds, h_prob, round(s_hedge, 4), 0.0),
                ],
                expected_roi=round(exp_roi, 4),
                win_prob_combined=round(target_prob + h_prob, 4),
                total_ev=round(exp_return - 1, 4),
                kelly_fraction=self._fractional_kelly(ev, o_ref),
                rationale=(
                    f"模型与市场分歧显著 (KL={signal.kl_divergence:.4f}, "
                    f"爆冷概率{signal.upset_probability:.0%})。"
                    f"模型看好{label_map.get(target, target)}(概率{target_prob:.0%} vs 市场{market_spf.get(target, 0):.0%})。"
                ),
            )]
            return results

        return []

    # ─────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────
    @staticmethod
    def _fractional_kelly(ev: float, odds: float) -> float:
        """Fractional Kelly (1/4) 仓位计算"""
        if ev <= 0 or odds <= 1.01:
            return 0.0
        p = 1.0 - 1.0 / odds  # 隐含概率
        q = 1.0 - p
        kelly_full = (p * odds - 1.0) / (odds - 1.0)
        kelly_fractional = kelly_full * 0.25  # 1/4 Kelly
        return max(0.0, min(kelly_fractional, HedgedPortfolioGenerator.MAX_TOTAL_STAKE))

    @staticmethod
    def _get_htft_odds(selection: str) -> float:
        """获取半全场参考赔率"""
        htft_odds_map = {
            "主主": 2.5, "主平": 13.0, "主客": 35.0,
            "平主": 5.0, "平平": 5.5, "平客": 11.0,
            "客主": 25.0, "客平": 13.0, "客客": 4.0,
        }
        return htft_odds_map.get(selection, 8.0)

    @staticmethod
    def _odds_to_market_probs(o_h: float, o_d: float, o_a: float) -> Dict[str, float]:
        """从赔率计算归一化市场隐含概率"""
        implied = [
            1.0 / max(o_h, 1.01),
            1.0 / max(o_d, 1.01),
            1.0 / max(o_a, 1.01),
        ]
        total = sum(implied)
        if total <= 0:
            return {"home": 1/3, "draw": 1/3, "away": 1/3}
        return {"home": implied[0]/total, "draw": implied[1]/total, "away": implied[2]/total}
