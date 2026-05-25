"""
最优策略管线 — 校准→边际→过滤→仓位→风控→输出。

替代旧的 5 策略并行模型估算 (Kelly/Conservative/EV Max/Probability/Combo)，
使用一条管线 × 四个风险档位。

管线: 原始概率 → 校准修正 → 边际计算 → 过滤 → 仓位优化 → 风控检查 → 输出

四级风险档位:
- conservative (稳健): 校准概率≥50%, 赔率1.6-2.5, 边际≥3%, 1/8 Kelly
- balanced (均衡): 校准概率≥40%, 赔率≤3.5, 边际≥3%, 1/4 Kelly  [默认]
- aggressive (进取): 校准概率≥35%, 任意赔率, 边际≥0, 1/4 Kelly
- speculative (激进): 校准概率≥25%, 任意赔率, 边际≥0, 1/2 Kelly

用法:
    from strategy.strategy_pipeline import StrategyPipeline
    pipeline = StrategyPipeline(risk_tier="balanced")
    picks = pipeline.generate(predictions, odds_home=1.80, odds_draw=3.50, odds_away=4.20)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum

from calibrator import Calibrator, CalibrationCurve
from strategy.edge_calculator import EdgeCalculator, MatchEdgeResult, EdgeResult
from strategy.position_sizer import PositionSizer, StakeResult
from strategy.risk_manager import RiskManager, RiskAssessment


class RiskTier(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    SPECULATIVE = "speculative"
    ADVISOR = "advisor"

# ─── 风险档位过滤参数 ───
TIER_FILTERS: Dict[RiskTier, Dict] = {
    RiskTier.CONSERVATIVE: {
        "min_calibrated_prob": 0.50,
        "min_odds": 1.60,
        "max_odds": 2.50,
        "min_edge": 0.03,
    },
    RiskTier.BALANCED: {
        "min_calibrated_prob": 0.40,
        "min_odds": 1.01,
        "max_odds": 3.50,
        "min_edge": 0.03,
    },
    RiskTier.AGGRESSIVE: {
        "min_calibrated_prob": 0.40,
        "min_odds": 1.01,
        "max_odds": 5.00,
        "min_edge": 0.05,
    },
    RiskTier.SPECULATIVE: {
        "min_calibrated_prob": 0.40,
        "min_odds": 1.01,
        "max_odds": 99.0,
        "min_edge": 0.05,
    },
    RiskTier.ADVISOR: {
        "min_calibrated_prob": 0.01, # 只要有概率
        "min_odds": 1.01,
        "max_odds": 99.0,
        "min_edge": -0.99, # 只要 EV > 0 (在 _passes_filter 中有 EV > 0 检查)
    },
}

# ─── 玩法赔率映射 (无真实赔率时的参考值) ───
SCORE_REFERENCE_ODDS = {
    "0:0": 8.0, "1:0": 6.0, "0:1": 7.0, "1:1": 6.5,
    "2:0": 7.5, "0:2": 9.0, "2:1": 7.5, "1:2": 8.5,
    "2:2": 13.0, "3:0": 11.0, "0:3": 16.0, "3:1": 10.0,
    "1:3": 13.0, "3:2": 14.0, "2:3": 16.0,
    "4:0": 18.0, "0:4": 28.0, "4:1": 15.0,
}

GOALS_REFERENCE_ODDS = {
    "0": 9.0, "1": 5.5, "2": 3.8, "3": 3.6,
    "4": 4.5, "5": 6.5, "6": 10.0, "7+": 12.0,
}

HALF_REFERENCE_ODDS = {
    "主主": 3.0, "主平": 13.0, "主客": 35.0,
    "平主": 5.0, "平平": 5.5, "平客": 11.0,
    "客主": 25.0, "客平": 13.0, "客客": 4.0,
}

PLAY_LABELS = {
    "SPF": "胜平负", "RQ": "让球", "SCORE": "比分",
    "GOALS": "总进球", "HALF": "半全场",
}

SELECTION_LABELS = {
    "home": "主胜", "draw": "平局", "away": "客胜",
    "home_home": "主主", "home_draw": "主平", "home_away": "主客",
    "draw_home": "平主", "draw_draw": "平平", "draw_away": "平客",
    "away_home": "客主", "away_draw": "客平", "away_away": "客客",
}


@dataclass(frozen=True)
class OptimalPick:
    """最优策略模型估算"""
    strategy_name: str
    risk_tier: str
    play_type: str
    play_label: str
    selection: str
    selection_label: str
    model_prob_raw: float
    model_prob_calibrated: float
    market_prob: float
    edge: float
    ev: float
    odds: float
    kelly_raw: float
    stake_pct: float
    stake_amount: float
    risk_score: float
    risk_label: str
    var_95: float
    cvar_95: float
    confidence: str
    rationale: str
    is_recommended: bool


class StrategyPipeline:
    """
    最优策略管线。

    用法:
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[
                {"play_type": "spf", "probabilities": {"home": 0.58, "draw": 0.24, "away": 0.18}},
            ],
            odds_home=1.80, odds_draw=3.50, odds_away=4.20,
            competition="EPL",
        )
    """

    # 风险档位 → RiskManager 暴露度限制 (与 PositionSizer max_stake_pct 对齐)
    TIER_RISK_LIMITS: Dict[RiskTier, Dict] = {
        RiskTier.CONSERVATIVE: {
            "single_match_max": 0.05,
            "single_round_max": 0.12,
            "single_league_max": 0.25,
            "total_max": 0.50,
        },
        RiskTier.BALANCED: {
            "single_match_max": 0.08,
            "single_round_max": 0.15,
            "single_league_max": 0.30,
            "total_max": 0.60,
        },
        RiskTier.AGGRESSIVE: {
            "single_match_max": 0.08,
            "single_round_max": 0.15,
            "single_league_max": 0.30,
            "total_max": 0.60,
        },
        RiskTier.SPECULATIVE: {
            "single_match_max": 0.10,
            "single_round_max": 0.20,
            "single_league_max": 0.40,
            "total_max": 0.80,
        },
        RiskTier.ADVISOR: {
            "single_match_max": 0.10,
            "single_round_max": 0.20,
            "single_league_max": 0.40,
            "total_max": 0.80,
        },
    }

    def __init__(
        self,
        risk_tier: str = "balanced",
        bankroll: float = 1000.0,
        peak: Optional[float] = None,
        calibrator: Optional[Calibrator] = None,
        risk_manager: Optional[RiskManager] = None,
    ):
        self._tier = RiskTier(risk_tier)
        self._sizer = PositionSizer(self._tier)
        self._calibrator = calibrator or Calibrator()
        self._edge_calc = EdgeCalculator()
        if risk_manager is not None:
            self._risk_mgr = risk_manager
        else:
            limits = self.TIER_RISK_LIMITS[self._tier]
            self._risk_mgr = RiskManager(bankroll=bankroll, peak=peak, limits=limits)
        self._filters = TIER_FILTERS[self._tier]

    @property
    def risk_tier(self) -> RiskTier:
        return self._tier

    def generate(
        self,
        predictions: List[Dict[str, Any]],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        competition: str = "",
        match_id: int = 0,
    ) -> List[OptimalPick]:
        """
        生成最优策略模型估算。

        Args:
            predictions: [{"play_type": "spf", "probabilities": {...}}, ...]
            odds_home/draw/away: 赔率
            competition: 联赛名
            match_id: 比赛ID

        Returns:
            List[OptimalPick] — 每种玩法最多 1 个模型估算
        """
        picks: List[OptimalPick] = []

        for pred in predictions:
            ptype = pred.get("play_type", "")
            raw_probs = pred.get("probabilities", {})
            if not raw_probs or not isinstance(raw_probs, dict):
                continue

            pick = self._process_play_type(
                ptype, raw_probs,
                odds_home, odds_draw, odds_away,
                competition, match_id,
            )
            if pick is not None:
                picks.append(pick)

        # 按边际排序, 最优的排在前面
        picks.sort(key=lambda p: p.edge, reverse=True)

        # 只保留 is_recommended 的模型估算
        return picks

    def _process_play_type(
        self,
        play_type: str,
        raw_probs: Dict[str, float],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
        competition: str,
        match_id: int,
    ) -> Optional[OptimalPick]:
        """处理单个玩法类型。"""
        if play_type == "SPF":
            return self._process_spf(
                raw_probs, odds_home, odds_draw, odds_away,
                competition, match_id,
            )
        elif play_type == "RQ":
            return self._process_rq(
                raw_probs, odds_home, odds_draw, odds_away,
                competition, match_id,
            )
        elif play_type in ("SCORE", "GOALS", "HALF"):
            return self._process_exotic(
                play_type, raw_probs,
                odds_home, odds_draw, odds_away,
                competition, match_id,
            )
        return None

    def _process_spf(
        self,
        raw_probs: Dict[str, float],
        odds_home: float, odds_draw: float, odds_away: float,
        competition: str, match_id: int,
    ) -> Optional[OptimalPick]:
        """处理 SPF (胜平负) 玩法。"""
        # 1. 校准
        cal_probs = self._calibrator.calibrate_spf(raw_probs)

        # 2. 边际
        edge_result = self._edge_calc.compute(odds_home, odds_draw, odds_away, cal_probs)

        # 3. 找最优选项
        if not edge_result.best_selection:
            return None

        best_sel = edge_result.best_selection
        e = edge_result.edges[best_sel]

        # 4. 过滤
        if not self._passes_filter(e):
            return None

        # 5. 仓位
        stake_result = self._sizer.compute(
            e.calibrated_prob, e.odds,
            bankroll=self._risk_mgr.bankroll,
            peak=self._risk_mgr.peak,
        )

        # 6. 风控
        allowed = self._risk_mgr.check(
            league=competition, stake_pct=stake_result.stake_pct,
        )

        # 7. 风险评估
        assessment = self._risk_mgr.assess(
            e.calibrated_prob, e.odds, e.edge,
            edge_result.overround_pct, stake_result.stake_pct,
        )

        # 8. 构建输出
        raw_p = raw_probs.get(best_sel, 0)
        confidence = self._confidence(e.calibrated_prob, e.edge)
        rationale = self._build_rationale(best_sel, e, stake_result, assessment)

        return OptimalPick(
            strategy_name=f"optimal_{self._tier.value}",
            risk_tier=self._tier.value,
            play_type="SPF",
            play_label=PLAY_LABELS["SPF"],
            selection=best_sel,
            selection_label=SELECTION_LABELS.get(best_sel, best_sel),
            model_prob_raw=raw_p,
            model_prob_calibrated=e.calibrated_prob,
            market_prob=e.market_prob,
            edge=e.edge,
            ev=e.ev,
            odds=e.odds,
            kelly_raw=stake_result.kelly_raw,
            stake_pct=stake_result.stake_pct,
            stake_amount=stake_result.stake_amount,
            risk_score=assessment.risk_score,
            risk_label=assessment.risk_label,
            var_95=assessment.var_95,
            cvar_95=assessment.cvar_95,
            confidence=confidence,
            rationale=rationale,
            is_recommended=allowed and e.ev > 0,
        )

    def _process_rq(
        self,
        raw_probs: Dict[str, float],
        odds_home: float, odds_draw: float, odds_away: float,
        competition: str, match_id: int,
    ) -> Optional[OptimalPick]:
        """处理 RQ (让球胜平负) 玩法。RQ 概率直接来自 Poisson, 通常不做校准。"""
        cal_probs = self._calibrator.calibrate_spf(raw_probs)
        edge_result = self._edge_calc.compute(odds_home, odds_draw, odds_away, cal_probs)

        if not edge_result.best_selection:
            return None

        best_sel = edge_result.best_selection
        e = edge_result.edges[best_sel]

        if not self._passes_filter(e):
            return None

        stake_result = self._sizer.compute(
            e.calibrated_prob, e.odds,
            bankroll=self._risk_mgr.bankroll,
            peak=self._risk_mgr.peak,
        )

        assessment = self._risk_mgr.assess(
            e.calibrated_prob, e.odds, e.edge,
            edge_result.overround_pct, stake_result.stake_pct,
        )

        raw_p = raw_probs.get(best_sel, 0)
        confidence = self._confidence(e.calibrated_prob, e.edge)
        rationale = self._build_rationale(best_sel, e, stake_result, assessment)

        return OptimalPick(
            strategy_name=f"optimal_{self._tier.value}",
            risk_tier=self._tier.value,
            play_type="RQ",
            play_label=PLAY_LABELS["RQ"],
            selection=best_sel,
            selection_label=SELECTION_LABELS.get(best_sel, best_sel),
            model_prob_raw=raw_p,
            model_prob_calibrated=e.calibrated_prob,
            market_prob=e.market_prob,
            edge=e.edge,
            ev=e.ev,
            odds=e.odds,
            kelly_raw=stake_result.kelly_raw,
            stake_pct=stake_result.stake_pct,
            stake_amount=stake_result.stake_amount,
            risk_score=assessment.risk_score,
            risk_label=assessment.risk_label,
            var_95=assessment.var_95,
            cvar_95=assessment.cvar_95,
            confidence=confidence,
            rationale=rationale,
            is_recommended=e.ev > 0,
        )

    def _process_exotic(
        self,
        play_type: str,
        raw_probs: Dict[str, float],
        odds_home: float, odds_draw: float, odds_away: float,
        competition: str, match_id: int,
    ) -> Optional[OptimalPick]:
        """处理 Score/Goals/Half 等赔率参考玩法。"""
        # 校准
        cal_probs = self._calibrator.calibrate_multi(raw_probs)

        # 取 Top 5
        top_items = sorted(cal_probs.items(), key=lambda x: x[1], reverse=True)[:5]

        best: Optional[Dict] = None
        for sel, cal_p in top_items:
            o = self._get_reference_odds(play_type, sel)
            if o <= 1.01:
                continue
            ev = cal_p * o - 1.0
            if ev > 0 and cal_p >= self._filters["min_calibrated_prob"]:
                if best is None or ev > best["ev"]:
                    best = {"sel": sel, "cal_p": cal_p, "odds": o, "ev": ev}

        if best is None:
            return None

        stake_result = self._sizer.compute(
            best["cal_p"], best["odds"],
            bankroll=self._risk_mgr.bankroll,
            peak=self._risk_mgr.peak,
        )

        assessment = self._risk_mgr.assess(
            best["cal_p"], best["odds"], 0.01,
            0.05, stake_result.stake_pct,
        )

        raw_p = raw_probs.get(best["sel"], 0)
        confidence = self._confidence(best["cal_p"], 0.01)

        return OptimalPick(
            strategy_name=f"optimal_{self._tier.value}",
            risk_tier=self._tier.value,
            play_type=play_type,
            play_label=PLAY_LABELS.get(play_type, play_type),
            selection=best["sel"],
            selection_label=SELECTION_LABELS.get(best["sel"], best["sel"]),
            model_prob_raw=raw_p,
            model_prob_calibrated=best["cal_p"],
            market_prob=0.0,
            edge=0.01,
            ev=best["ev"],
            odds=best["odds"],
            kelly_raw=stake_result.kelly_raw,
            stake_pct=stake_result.stake_pct,
            stake_amount=stake_result.stake_amount,
            risk_score=assessment.risk_score,
            risk_label=assessment.risk_label,
            var_95=assessment.var_95,
            cvar_95=assessment.cvar_95,
            confidence=confidence,
            rationale=f"{PLAY_LABELS.get(play_type, play_type)}最优选项, 校准概率{best['cal_p']:.1%}, EV={best['ev']:+.1%}",
            is_recommended=best["ev"] > 0,
        )

    def _passes_filter(self, edge: EdgeResult) -> bool:
        """检查是否通过风险档位过滤条件。"""
        f = self._filters
        if edge.calibrated_prob < f["min_calibrated_prob"]:
            return False
        if edge.odds < f["min_odds"] or edge.odds > f["max_odds"]:
            return False
        if edge.edge < f["min_edge"]:
            return False
        if edge.ev <= 0:
            return False
        return True

    @staticmethod
    def _confidence(calibrated_prob: float, edge: float) -> str:
        """置信度评估。"""
        if calibrated_prob >= 0.60 and edge >= 0.05:
            return "high"
        elif calibrated_prob >= 0.45 and edge >= 0.02:
            return "medium"
        else:
            return "low"

    @staticmethod
    def _build_rationale(
        sel: str,
        edge: EdgeResult,
        stake: StakeResult,
        assessment: RiskAssessment,
    ) -> str:
        """生成模型估算理由。"""
        label = SELECTION_LABELS.get(sel, sel)
        parts = [
            f"校准概率{edge.calibrated_prob:.1%} vs 市场{edge.market_prob:.1%}",
            f"边际{edge.edge:+.1%}",
            f"EV={edge.ev:+.1%}",
        ]
        if stake.dd_factor < 1.0:
            parts.append(f"回撤缩减×{stake.dd_factor}")
        if assessment.risk_label in ("high", "extreme"):
            parts.append(f"风险{assessment.risk_label}")
        return " | ".join(parts)

    @staticmethod
    def _get_reference_odds(play_type: str, selection: str) -> float:
        """获取参考赔率 (无真实赔率时使用)。"""
        if play_type == "SCORE":
            return SCORE_REFERENCE_ODDS.get(selection, 25.0)
        elif play_type == "GOALS":
            return GOALS_REFERENCE_ODDS.get(str(selection), 8.0)
        elif play_type == "HALF":
            return HALF_REFERENCE_ODDS.get(selection, 15.0)
        return 2.0
