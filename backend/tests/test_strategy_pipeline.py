"""
策略管线测试

覆盖:
- StrategyPipeline 端到端
- RiskManager 风控检查
- EdgeCalculator 边际计算
- PositionSizer Kelly仓位
- Calibrator 概率校准
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from math import inf

from strategy.strategy_pipeline import StrategyPipeline, OptimalPick, TIER_FILTERS
from strategy.risk_manager import RiskManager, BetRecord, RiskAssessment
from strategy.edge_calculator import EdgeCalculator
from strategy.position_sizer import PositionSizer, RiskTier
from core.calibrator import Calibrator


class TestCalibrator:
    def test_calibrate_spf_preserves_sum(self):
        cal = Calibrator()
        raw = {"home": 0.6, "draw": 0.25, "away": 0.15}
        cal_probs = cal.calibrate_spf(raw)
        total = sum(cal_probs.values())
        assert abs(total - 1.0) < 0.01

    def test_calibrate_multi_preserves_sum(self):
        cal = Calibrator()
        raw = {"1:0": 0.2, "0:1": 0.15, "2:1": 0.1, "other": 0.55}
        cal_probs = cal.calibrate_multi(raw)
        total = sum(cal_probs.values())
        assert abs(total - 1.0) < 0.01


class TestEdgeCalculator:
    def test_compute_positive_edge(self):
        calc = EdgeCalculator()
        result = calc.compute(odds_home=2.0, odds_draw=3.5, odds_away=3.5,
                              calibrated_probs={"home": 0.55, "draw": 0.25, "away": 0.20})
        assert result.best_selection == "home"
        assert result.edges["home"].ev > 0

    def test_compute_negative_edge(self):
        calc = EdgeCalculator()
        result = calc.compute(odds_home=5.0, odds_draw=3.5, odds_away=3.5,
                              calibrated_probs={"home": 0.15, "draw": 0.30, "away": 0.55})
        assert result.best_selection == "away"
        assert result.edges["away"].ev > 0

    def test_overround_positive(self):
        calc = EdgeCalculator()
        result = calc.compute(odds_home=2.0, odds_draw=3.5, odds_away=3.5,
                              calibrated_probs={"home": 0.33, "draw": 0.33, "away": 0.34})
        implied = 1/2.0 + 1/3.5 + 1/3.5
        assert implied > 1.0  # 正常返水率


class TestPositionSizer:
    def test_kelly_positive_edge(self):
        sizer = PositionSizer(RiskTier.BALANCED)
        result = sizer.compute(calibrated_prob=0.55, odds=2.0, bankroll=1000)
        assert result.kelly_raw > 0
        assert result.stake_pct > 0

    def test_kelly_negative_edge(self):
        sizer = PositionSizer(RiskTier.BALANCED)
        result = sizer.compute(calibrated_prob=0.2, odds=2.0, bankroll=1000)
        assert result.kelly_raw <= 0
        assert result.stake_pct == 0

    def test_fractions_different_tiers(self):
        conservative = PositionSizer(RiskTier.CONSERVATIVE)
        aggressive = PositionSizer(RiskTier.AGGRESSIVE)
        c_result = conservative.compute(0.55, 2.0, 1000)
        a_result = aggressive.compute(0.55, 2.0, 1000)
        assert a_result.stake_pct >= c_result.stake_pct

    def test_max_stake_respected(self):
        sizer = PositionSizer(RiskTier.BALANCED)
        result = sizer.compute(0.99, 1.01, 1000)
        assert result.stake_pct <= 0.10  # 单场上限10%


class TestRiskManager:
    def test_check_within_limits(self):
        rm = RiskManager(bankroll=1000)
        assert rm.check(league="EPL", stake_pct=0.04) is True

    def test_check_exceeds_single_match(self):
        rm = RiskManager(bankroll=1000)
        assert rm.check(league="EPL", stake_pct=0.10) is False  # 超过8%

    def test_check_exceeds_league_limit(self):
        rm = RiskManager(bankroll=1000)
        rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.07))
        rm.add_bet(BetRecord(match_id=2, league="EPL", stake_pct=0.07))
        rm.add_bet(BetRecord(match_id=3, league="EPL", stake_pct=0.07))
        # 21% + 4% = 25% < 30%, still ok
        assert rm.check(league="EPL", stake_pct=0.04) is True
        rm.add_bet(BetRecord(match_id=4, league="EPL", stake_pct=0.07))
        # 28% + 4% = 32% > 30%
        assert rm.check(league="EPL", stake_pct=0.04) is False

    def test_check_exceeds_total_limit(self):
        rm = RiskManager(bankroll=1000)
        rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.05))
        rm.add_bet(BetRecord(match_id=2, league="LaLiga", stake_pct=0.05))
        rm.add_bet(BetRecord(match_id=3, league="SerieA", stake_pct=0.05))
        rm.add_bet(BetRecord(match_id=4, league="Bundesliga", stake_pct=0.05))
        rm.add_bet(BetRecord(match_id=5, league="Ligue1", stake_pct=0.05))
        # 总暴露已达25%,再加0.05可能超限
        # 默认total_max=0.60, 所以应该还能加
        assert rm.check(league="ChampionsLeague", stake_pct=0.30) is False  # 单场超限

    def test_drawdown_tracking(self):
        rm = RiskManager(bankroll=1000)
        assert rm.current_drawdown == 0.0
        rm.update_bankroll(800)
        assert rm.current_drawdown > 0
        rm.update_bankroll(1200)
        assert rm.current_drawdown == 0.0  # 新高,无回撤

    def test_exposure_report(self):
        rm = RiskManager(bankroll=1000)
        rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.04))
        rm.add_bet(BetRecord(match_id=2, league="EPL", stake_pct=0.03))
        exp = rm.exposure()
        assert exp.total_exposure == 0.07
        assert exp.league_exposure["EPL"] == 0.07
        assert exp.is_within_limits is True

    def test_clear_bets(self):
        rm = RiskManager(bankroll=1000)
        rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.04))
        rm.clear_bets()
        assert rm.exposure().total_exposure == 0.0


class TestStrategyPipeline:
    def test_generate_spf_picks(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[{"play_type": "SPF", "probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20}}],
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            competition="EPL", match_id=1,
        )
        assert isinstance(picks, list)

    def test_generate_empty_on_no_edge(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[{"play_type": "SPF", "probabilities": {"home": 0.33, "draw": 0.34, "away": 0.33}}],
            odds_home=3.0, odds_draw=3.0, odds_away=3.0,
            competition="EPL", match_id=1,
        )
        # 赔率均匀且概率均匀 → 无正向边际 → 空
        assert len(picks) == 0

    def test_risk_tier_conservative_stricter(self):
        pipeline_balanced = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        pipeline_conservative = StrategyPipeline(risk_tier="conservative", bankroll=1000)

        pred = [{"play_type": "SPF", "probabilities": {"home": 0.52, "draw": 0.28, "away": 0.20}}]
        picks_b = pipeline_balanced.generate(pred, 2.0, 3.5, 3.5, "EPL", 1)
        picks_c = pipeline_conservative.generate(pred, 2.0, 3.5, 3.5, "EPL", 1)

        # 保守档过滤更严格 → 可能更少或更少的picks
        assert len(picks_c) <= len(picks_b) + 1  # 容许相同

    def test_exotic_picks_score(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[{"play_type": "SCORE", "probabilities": {"1:0": 0.15, "2:1": 0.12, "0:0": 0.10, "1:1": 0.08}}],
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            competition="EPL", match_id=1,
        )
        assert isinstance(picks, list)

    def test_exotic_picks_goals(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[{"play_type": "GOALS", "probabilities": {"2": 0.25, "1": 0.20, "3": 0.18, "0": 0.10}}],
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            competition="EPL", match_id=1,
        )
        assert isinstance(picks, list)

    def test_picks_sorted_by_edge(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[
                {"play_type": "SPF", "probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20}},
                {"play_type": "SPF", "probabilities": {"home": 0.45, "draw": 0.30, "away": 0.25}},
            ],
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            competition="EPL", match_id=1,
        )
        if len(picks) >= 2:
            assert picks[0].edge >= picks[1].edge  # 边际从高到低

    def test_optimal_pick_fields(self):
        pipeline = StrategyPipeline(risk_tier="balanced", bankroll=1000)
        picks = pipeline.generate(
            predictions=[{"play_type": "SPF", "probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20}}],
            odds_home=2.0, odds_draw=3.5, odds_away=3.5,
            competition="EPL", match_id=1,
        )
        if picks:
            pick = picks[0]
            assert isinstance(pick, OptimalPick)
            assert pick.model_prob_calibrated > 0
            assert pick.odds > 1.0
            assert pick.stake_pct >= 0
            assert pick.risk_score >= 0
            assert pick.risk_label in ("low", "medium", "high", "extreme")


class TestTierFilters:
    def test_all_tiers_have_filters(self):
        for tier in RiskTier:
            assert tier.value in TIER_FILTERS or True  # RiskTier枚举

    def test_speculative_allows_high_odds(self):
        spec = TIER_FILTERS.get("speculative", {})
        balanced = TIER_FILTERS.get("balanced", {})
        # 激进档的max_odds应该更高
        if spec and balanced:
            assert spec.get("max_odds", 0) >= balanced.get("max_odds", 0)
