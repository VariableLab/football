from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from .strategy_pipeline import SCORE_REFERENCE_ODDS

@dataclass
class PortfolioLeg:
    type: str  # "primary" or "hedge"
    play: str
    selection: str
    odds: float
    probability: float
    stake_pct: float

@dataclass
class PortfolioRecommendation:
    strategy_type: str
    name: str
    legs: List[PortfolioLeg]
    expected_roi: float
    win_prob_combined: float
    rationale: str

class HedgedPortfolioGenerator:
    """
    Cross-Market Hedged Portfolio Strategy Generator.
    Identifies high-yield selections (Correct Score or HT/FT) and pairs them with
    a hedging bet (Match Odds SPF) to protect capital while maximizing EV.
    """
    def __init__(self, match_predictions: List[Dict[str, Any]], odds_home: float, odds_draw: float, odds_away: float):
        self.preds = match_predictions
        self.odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}

    def _get_play_probs(self, play_type: str) -> Dict[str, float]:
        for p in self.preds:
            if p.get("play_type") == play_type:
                return p.get("probabilities", {})
        return {}

    def generate(self, min_ev: float = 0.05) -> List[PortfolioRecommendation]:
        portfolios = []
        score_probs = self._get_play_probs("SCORE")
        spf_probs = self._get_play_probs("SPF")
        
        # 1. Correct Score + SPF Hedging
        # Look for high-yield correct score (e.g. 1:0, 2:0)
        for score, prob in score_probs.items():
            if prob < 0.05: continue
            
            odds_high = SCORE_REFERENCE_ODDS.get(score, 10.0)
            ev_raw = prob * odds_high - 1.0
            
            if ev_raw < min_ev: continue
            
            # Determine hedge: if we bet home win score, hedge with draw or away
            # But the user specifically asked: "用最简单的胜负来对冲风险"
            # If we bet Score 1:0, we can hedge with Draw (if we think 0:0 or 1:1 is likely)
            # Or if we bet Draw/Win (HT/FT), hedge with Home Win (wait, that overlaps, see logic below).
            
            # Strategy: Score + Draw Hedge
            # High-yield: Score 1:0 or 2:0
            # Hedge: Draw (odds_draw)
            if score in ["1:0", "2:0", "2:1"]:
                hedge_odds = self.odds["draw"]
                hedge_prob = spf_probs.get("draw", 0.0)
                hedge_selection = "draw"
                hedge_play = "SPF"
            elif score in ["0:1", "0:2", "1:2"]:
                hedge_odds = self.odds["draw"]
                hedge_prob = spf_probs.get("draw", 0.0)
                hedge_selection = "draw"
                hedge_play = "SPF"
            else:
                continue

            # Calculate Stakes (Full capital protection on Hedge)
            # S_hedge * hedge_odds = 1.0 => S_hedge = 1.0 / hedge_odds
            s_hedge = 1.0 / hedge_odds
            if s_hedge >= 0.9: continue # Not worth it, odds too low
            
            s_primary = 1.0 - s_hedge
            
            # Expected Return
            # P(Score) * (s_primary * odds_high) + P(Hedge) * (s_hedge * hedge_odds) - 1.0
            # Since s_hedge * hedge_odds = 1.0, the hedge outcome returns 1.0. Net profit on hedge = 0.
            # So ROI = P(Score) * (s_primary * odds_high) + P(Hedge) * 1.0 + P(Loss) * 0 - 1.0
            expected_return = prob * (s_primary * odds_high) + hedge_prob * 1.0
            expected_roi = expected_return - 1.0
            
            if expected_roi > min_ev:
                portfolios.append(PortfolioRecommendation(
                    strategy_type="Hedged",
                    name="比分高赔狙击 + 平局底仓防守",
                    legs=[
                        PortfolioLeg("primary", "SCORE", score, odds_high, prob, s_primary),
                        PortfolioLeg("hedge", hedge_play, hedge_selection, hedge_odds, hedge_prob, s_hedge)
                    ],
                    expected_roi=expected_roi,
                    win_prob_combined=prob + hedge_prob,
                    rationale=f"主打比分{score}博取高额回报，利用平局赔率{hedge_odds}保本防守。整体预期收益 {expected_roi*100:.1f}%。"
                ))
                
        # 1. Multiple Correct Scores + SPF Hedging
        # High-yield: Top 3 Score predictions for Home Win
        home_scores = ["1:0", "2:0", "2:1", "3:0", "3:1"]
        away_scores = ["0:1", "0:2", "1:2", "0:3", "1:3"]
        
        # Sort home scores by prob
        home_score_probs = {s: score_probs.get(s, 0.0) for s in home_scores}
        top_home_scores = sorted(home_score_probs.items(), key=lambda x: x[1], reverse=True)[:2]
        
        # If the top 2 home scores have a combined prob > 0.15
        if sum(p for s, p in top_home_scores) > 0.15:
            s1, p1 = top_home_scores[0]
            s2, p2 = top_home_scores[1]
            o1 = SCORE_REFERENCE_ODDS.get(s1, 7.0)
            o2 = SCORE_REFERENCE_ODDS.get(s2, 7.0)
            
            # Hedge with Draw
            hedge_odds = self.odds["draw"]
            hedge_prob = spf_probs.get("draw", 0.0)
            
            # Dutching Math: S_hedge = 1/hedge_odds. S_s1 = C / o1, S_s2 = C / o2
            # S_hedge + S_s1 + S_s2 = 1.0
            # Let remaining stake R = 1.0 - S_hedge
            s_hedge = 1.0 / hedge_odds if hedge_odds > 0 else 0
            if s_hedge < 0.8:
                R = 1.0 - s_hedge
                # split R proportional to 1/odds to equalize profit if either score hits
                inv_o1, inv_o2 = 1.0/o1, 1.0/o2
                sum_inv = inv_o1 + inv_o2
                s_s1 = R * (inv_o1 / sum_inv)
                s_s2 = R * (inv_o2 / sum_inv)
                
                exp_return = p1 * (s_s1 * o1) + p2 * (s_s2 * o2) + hedge_prob * 1.0
                exp_roi = exp_return - 1.0
                
                if exp_roi > min_ev:
                    portfolios.append(PortfolioRecommendation(
                        strategy_type="HedgedScore",
                        name="双选比分 + 平局保本",
                        legs=[
                            PortfolioLeg("primary", "SCORE", s1, o1, p1, s_s1),
                            PortfolioLeg("primary", "SCORE", s2, o2, p2, s_s2),
                            PortfolioLeg("hedge", "SPF", "draw", hedge_odds, hedge_prob, s_hedge)
                        ],
                        expected_roi=exp_roi,
                        win_prob_combined=p1 + p2 + hedge_prob,
                        rationale=f"主攻比分 {s1} & {s2}，平局(赔率{hedge_odds})打出则全额保本。预期收益 {exp_roi*100:.1f}%。"
                    ))

        # 2. Half/Full (HT/FT) + Opposite Hedge
        half_probs = self._get_play_probs("HALF")
        # Find high prob "Draw/Home" or "Draw/Away"
        for hf_sel, prob in half_probs.items():
            if prob < 0.08: continue
            
            odds_high = 5.0 # reference odds for draw/win
            if hf_sel == "平主":
                # Hedge with Draw and Away Win
                hedge_odds_1 = self.odds["draw"]
                hedge_prob_1 = spf_probs.get("draw", 0.0)
                hedge_odds_2 = self.odds["away"]
                hedge_prob_2 = spf_probs.get("away", 0.0)
                
                s_h1 = 1.0 / hedge_odds_1 if hedge_odds_1 > 0 else 0
                s_h2 = 1.0 / hedge_odds_2 if hedge_odds_2 > 0 else 0
                
                s_hedge_total = s_h1 + s_h2
                if s_hedge_total < 0.8:
                    s_primary = 1.0 - s_hedge_total
                    exp_return = prob * (s_primary * odds_high) + (hedge_prob_1 + hedge_prob_2) * 1.0
                    exp_roi = exp_return - 1.0
                    
                    if exp_roi > min_ev:
                        portfolios.append(PortfolioRecommendation(
                            strategy_type="HedgedHTFT",
                            name="半全场(平/主) + 平负保本",
                            legs=[
                                PortfolioLeg("primary", "HALF", hf_sel, odds_high, prob, s_primary),
                                PortfolioLeg("hedge", "SPF", "draw", hedge_odds_1, hedge_prob_1, s_h1),
                                PortfolioLeg("hedge", "SPF", "away", hedge_odds_2, hedge_prob_2, s_h2)
                            ],
                            expected_roi=exp_roi,
                            win_prob_combined=prob + hedge_prob_1 + hedge_prob_2,
                            rationale=f"预测主队下半场发力(平/主)。防冷平局和客胜保本。唯一死穴是主队早早领先(主/主)。预期收益 {exp_roi*100:.1f}%。"
                        ))
        portfolios.sort(key=lambda x: x.expected_roi, reverse=True)
        return portfolios
