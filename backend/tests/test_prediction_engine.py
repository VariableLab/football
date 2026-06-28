"""
预测引擎核心测试

覆盖:
- EloModel 基础预测
- PoissonModel Dixon-Coles 修正
- MarketModel 赔率隐含概率
- PlayerAdjustmentModel 球员修正
- FeatureBuilder 特征构建
- PredictionEngine 端到端预测
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np

from core.context import TeamContext, MatchContext, PredictionResult
from core.models.elo import EloModel
from core.models.poisson import PoissonModel
from core.models.market import MarketModel
from core.models.player_adjustment import PlayerAdjustmentModel
from core.models.form_adjustment import FormAdjustmentModel
from core.models.home_away import HomeAwayModel
from core.models.schedule_density import ScheduleDensityModel
from core.models.weather_venue import WeatherVenueModel
from core.models.tactical import TacticalModel
from core.models.coach_impact import CoachImpactModel
from core.models.squad_availability import SquadAvailabilityModel
from core.models.draw_detection import DrawDetectionModel
from core.constants import (
    MAX_GOALS, DIXON_COLES_RHO, DRAW_INFLATION_FACTOR,
    HT_FT_TRANSITION, KNOCKOUT_GOAL_FACTORS,
    WEATHER_PENALTY,
)
TACTICAL_MATRIX = TacticalModel.TACTICAL_MATRIX
from features.feature_builder import FeatureBuilder, FEATURE_DIM


# ─── Fixtures ───

@pytest.fixture
def basic_team():
    return TeamContext(team_id=1, name="Brazil", name_en="BRA", elo=1850, fifa_rank=5,
                       avg_goals_scored=1.8, avg_goals_conceded=0.7,
                       avg_xg=1.9, avg_xga=0.6, possession=58.0,
                       form_factor=1.1, key_players_available=11, key_players_total=11,
                       squad_fatigue_index=0.2, recent_results="WWDWW",
                       tactical_style="attack", coach_rating=0.7)


@pytest.fixture
def opponent_team():
    return TeamContext(team_id=2, name="Japan", name_en="JPN", elo=1600, fifa_rank=15,
                       avg_goals_scored=1.2, avg_goals_conceded=1.0,
                       avg_xg=1.3, avg_xga=1.0, possession=45.0,
                       form_factor=0.95, key_players_available=10, key_players_total=11,
                       squad_fatigue_index=0.4, recent_results="LWDLD",
                       tactical_style="counter", coach_rating=0.5)


@pytest.fixture
def match_ctx(basic_team, opponent_team):
    return MatchContext(
        match_id=1, home_team=basic_team, away_team=opponent_team,
        stage="group", is_knockout=False, handicap=0,
        odds_home=1.65, odds_draw=3.80, odds_away=5.20,
        closing_odds_home=1.70, closing_odds_draw=3.70, closing_odds_away=5.00,
        competition="World Cup", venue_type="neutral",
        weather="clear", temperature=25.0, pitch_condition="good",
    )


@pytest.fixture
def knockout_ctx(basic_team, opponent_team):
    return MatchContext(
        match_id=2, home_team=basic_team, away_team=opponent_team,
        stage="QF", is_knockout=True, handicap=-1,
        odds_home=1.50, odds_draw=4.00, odds_away=6.00,
        closing_odds_home=1.55, closing_odds_draw=3.90, closing_odds_away=5.80,
        competition="World Cup", venue_type="neutral",
    )


# ─── Constants Tests ───

class TestConstants:
    def test_max_goals(self):
        assert MAX_GOALS == 8

    def test_dixon_coles_rho(self):
        assert 0 < DIXON_COLES_RHO < 1

    def test_draw_inflation(self):
        assert DRAW_INFLATION_FACTOR > 1.0

    def test_ht_ft_transition_shape(self):
        for outcome in ["home", "draw", "away"]:
            assert outcome in HT_FT_TRANSITION
            total = sum(HT_FT_TRANSITION[outcome].values())
            assert abs(total - 1.0) < 0.01

    def test_tactical_matrix_structure(self):
        for (home_style, away_style), factors in TACTICAL_MATRIX.items():
            assert isinstance(factors, tuple) and len(factors) == 2
            assert 0.5 < factors[0] < 1.5
            assert 0.5 < factors[1] < 1.5

    def test_weather_penalty_range(self):
        for weather, pen in WEATHER_PENALTY.items():
            assert 0 <= pen <= 0.15

    def test_knockout_factors(self):
        for stage, factor in KNOCKOUT_GOAL_FACTORS.items():
            assert 0.7 < factor < 1.0


# ─── EloModel Tests ───

class TestEloModel:
    def test_equal_elo(self, match_ctx):
        match_ctx.home_team.elo = 1500
        match_ctx.away_team.elo = 1500
        result = EloModel.predict(match_ctx)
        assert abs(result["home"] - result["away"]) < 0.05

    def test_strong_home_wins(self, match_ctx):
        match_ctx.home_team.elo = 1900
        match_ctx.away_team.elo = 1500
        result = EloModel.predict(match_ctx)
        assert result["home"] > result["away"]
        assert result["home"] > 0.5

    def test_strong_away_wins(self, match_ctx):
        match_ctx.home_team.elo = 1500
        match_ctx.away_team.elo = 1900
        result = EloModel.predict(match_ctx)
        assert result["away"] > result["home"]

    def test_draw_probability_reasonable(self, match_ctx):
        match_ctx.home_team.elo = 1600
        match_ctx.away_team.elo = 1580
        result = EloModel.predict(match_ctx)
        assert 0.15 < result["draw"] < 0.35

    def test_knockout_draw_boost(self, match_ctx):
        match_ctx.is_knockout = True
        result = EloModel.predict(match_ctx)
        assert result["draw"] > 0.20

    def test_probabilities_sum_to_one(self, match_ctx):
        result = EloModel.predict(match_ctx)
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001

    def test_win_prob_formula(self):
        p = EloModel.win_prob(400)
        assert 0.85 < p < 0.95

    def test_win_prob_symmetric(self):
        p1 = EloModel.win_prob(200)
        p2 = EloModel.win_prob(-200)
        assert abs(p1 + p2 - 1.0) < 0.01


# ─── PoissonModel Tests ───

class TestPoissonModel:
    def test_score_matrix_sum_to_one(self, match_ctx):
        matrix, lh, la = PoissonModel.predict_score_matrix(match_ctx)
        total = matrix.sum()
        assert abs(total - 1.0) < 0.001

    def test_lambda_positive(self, match_ctx):
        matrix, lh, la = PoissonModel.predict_score_matrix(match_ctx)
        assert lh > 0 and la > 0

    def test_dixon_coles_tau(self):
        tau = PoissonModel._tau_dixon_coles(0, 0, 1.5, 1.2, 0.0092)
        assert tau < 1.0

    def test_dixon_coles_tau_high_scores(self):
        tau = PoissonModel._tau_dixon_coles(3, 2, 1.5, 1.2, 0.0092)
        assert abs(tau - 1.0) < 0.001

    def test_spf_only_reasonable(self, match_ctx):
        result = PoissonModel.predict_spf_only(match_ctx)
        assert abs(sum(result.values()) - 1.0) < 0.001
        assert 0.1 < result["home"] < 0.9

    def test_full_predict_shapes(self, match_ctx):
        result = PoissonModel.predict(match_ctx)
        assert "spf" in result
        assert "rq" in result
        assert "score" in result
        assert "goals" in result
        assert "half" in result
        assert "lambda_home" in result
        assert "lambda_away" in result

    def test_score_probs_sum_to_one(self, match_ctx):
        result = PoissonModel.predict(match_ctx)
        score_total = sum(result["score"].values())
        assert 0.8 < score_total < 1.2

    def test_goals_probs_sum_to_one(self, match_ctx):
        result = PoissonModel.predict(match_ctx)
        goals_total = sum(result["goals"].values())
        assert abs(goals_total - 1.0) < 0.01

    def test_rq_handicap_zero(self, match_ctx):
        result = PoissonModel.predict(match_ctx)
        rq_total = sum(v for k, v in result["rq"].items() if k != "handicap")
        assert abs(rq_total - 1.0) < 0.01

    def test_half_probs_sum_to_one(self, match_ctx):
        result = PoissonModel.predict(match_ctx)
        half_total = sum(result["half"].values())
        assert abs(half_total - 1.0) < 0.01


# ─── MarketModel Tests ───

class TestMarketModel:
    def test_predict_with_odds(self, match_ctx):
        result = MarketModel.predict(match_ctx)
        assert result is not None
        assert abs(sum(result.values()) - 1.0) < 0.01

    def test_predict_smoothing(self, match_ctx):
        match_ctx.closing_odds_home = 1.01
        match_ctx.closing_odds_draw = 15.0
        match_ctx.closing_odds_away = 20.0
        result = MarketModel.predict(match_ctx)
        assert result["home"] < 0.99

    def test_predict_no_odds(self):
        ctx = MatchContext(
            match_id=99, home_team=TeamContext(1, "A"),
            away_team=TeamContext(2, "B"),
        )
        result = MarketModel.predict(ctx)
        assert result is None


# ─── Adjustment Model Tests ───

class TestPlayerAdjustmentModel:
    def test_full_squad_returns_near_one(self, match_ctx):
        factor = PlayerAdjustmentModel.predict(match_ctx)
        assert 0.9 < factor < 1.1

    def test_missing_players_reduces_strength(self, match_ctx):
        match_ctx.home_team.key_players_available = 8
        match_ctx.home_team.key_players_total = 11
        factor = PlayerAdjustmentModel.predict(match_ctx)
        assert factor < 1.0


class TestFormAdjustmentModel:
    def test_good_form_increases_factor(self):
        team = TeamContext(1, "A", recent_results="WWWWW")
        factor = FormAdjustmentModel.compute_factor(team)
        assert factor > 1.0

    def test_bad_form_decreases_factor(self):
        team = TeamContext(1, "A", recent_results="LLLLL")
        factor = FormAdjustmentModel.compute_factor(team)
        assert factor < 1.0

    def test_no_form_returns_one(self):
        team = TeamContext(1, "A", recent_results="")
        factor = FormAdjustmentModel.compute_factor(team)
        assert abs(factor - 1.0) < 0.01


class TestScheduleDensityModel:
    def test_adequate_rest(self):
        team = TeamContext(1, "A")
        team.rest_days = 7
        team.squad_fatigue_index = 0.0
        factor = ScheduleDensityModel.compute_factor(team)
        assert factor >= 0.95

    def test_short_rest(self):
        team = TeamContext(1, "A")
        team.rest_days = 2
        factor = ScheduleDensityModel.compute_factor(team)
        assert factor < 1.0

    def test_fatigue_amplifies(self):
        team = TeamContext(1, "A")
        team.rest_days = 2
        team.squad_fatigue_index = 0.8
        factor = ScheduleDensityModel.compute_factor(team)
        assert factor < 0.95


class TestWeatherVenueModel:
    def test_clear_weather_no_penalty(self, match_ctx):
        match_ctx.weather = "clear"
        factor = WeatherVenueModel.compute_factor(match_ctx, match_ctx.home_team)
        assert factor >= 0.95

    def test_snow_penalty(self, match_ctx):
        match_ctx.weather = "snow"
        factor = WeatherVenueModel.compute_factor(match_ctx, match_ctx.home_team)
        assert factor < 0.95

    def test_adaptability_reduces_penalty(self, match_ctx):
        match_ctx.weather = "hot"
        match_ctx.home_team.weather_adaptability = 2.0
        factor = WeatherVenueModel.compute_factor(match_ctx, match_ctx.home_team)
        assert factor > 0.90


class TestTacticalModel:
    def test_attack_vs_defense(self):
        ctx = MatchContext(1,
            home_team=TeamContext(1, "A", tactical_style="attack"),
            away_team=TeamContext(2, "B", tactical_style="defense"))
        h, a = TacticalModel.compute_factors(ctx)
        assert h > a

    def test_defense_vs_attack(self):
        ctx = MatchContext(1,
            home_team=TeamContext(1, "A", tactical_style="defense"),
            away_team=TeamContext(2, "B", tactical_style="attack"))
        h, a = TacticalModel.compute_factors(ctx)
        assert a > h

    def test_balanced_vs_balanced(self):
        ctx = MatchContext(1,
            home_team=TeamContext(1, "A", tactical_style="balanced"),
            away_team=TeamContext(2, "B", tactical_style="balanced"))
        h, a = TacticalModel.compute_factors(ctx)
        assert abs(h - 1.0) < 0.01


class TestCoachImpactModel:
    def test_good_coach_knockout_bonus(self):
        team = TeamContext(1, "A", coach_rating=0.9)
        factor_normal = CoachImpactModel.compute_factor(team, is_knockout=False)
        factor_knockout = CoachImpactModel.compute_factor(team, is_knockout=True)
        assert factor_knockout >= factor_normal

    def test_bad_coach_penalty(self):
        team = TeamContext(1, "A", coach_rating=0.1)
        factor = CoachImpactModel.compute_factor(team)
        assert factor < 1.0


class TestSquadAvailabilityModel:
    def test_no_injuries(self):
        team = TeamContext(1, "A"); team.key_injuries = ""
        atk, def_ = SquadAvailabilityModel.compute_factor(team)
        assert abs(atk - 1.0) < 0.01
        assert abs(def_ - 1.0) < 0.01

    def test_injuries_reduce_attack(self):
        team = TeamContext(1, "A")
        team.key_injuries = "Messi(injured),Neymar(suspended)"
        atk, def_ = SquadAvailabilityModel.compute_factor(team)
        assert atk < 1.0


class TestHomeAwayModel:
    def test_neutral_venue(self, match_ctx):
        factor = HomeAwayModel.compute_factor(match_ctx, is_home=True)
        assert abs(factor - 1.0) < 0.01

    def test_home_advantage(self, match_ctx):
        match_ctx.venue_type = "home"
        factor = HomeAwayModel.compute_factor(match_ctx, is_home=True)
        assert factor >= 0.8


class TestDrawDetectionModel:
    def test_predict_returns_dict(self, match_ctx):
        spf = {"home": 0.5, "draw": 0.25, "away": 0.25}
        result = DrawDetectionModel.predict(spf, match_ctx)
        assert isinstance(result, dict)
        assert "home" in result and "draw" in result and "away" in result
        total = sum(result.values())
        assert 0.99 < total < 1.01


# ─── FeatureBuilder Tests ───

class TestFeatureBuilder:
    def test_build_returns_array(self, match_ctx):
        builder = FeatureBuilder(use_interactions=True)
        feats = builder.build(
            elo_probs={"home": 0.5, "draw": 0.3, "away": 0.2},
            poisson_result={"spf": {"home": 0.45, "draw": 0.28, "away": 0.27},
                           "lambda_home": 1.5, "lambda_away": 1.0},
            players_factor=1.0,
            market_probs={"home": 0.55, "draw": 0.25, "away": 0.20},
            form_features=None,
            h2h_features=None,
            ctx=match_ctx,
        )
        assert isinstance(feats, np.ndarray)
        assert feats.shape[0] == builder.get_input_dim()

    def test_input_dim_with_interactions(self):
        builder = FeatureBuilder(use_interactions=True)
        assert builder.get_input_dim() == FEATURE_DIM + 5

    def test_input_dim_without_interactions(self):
        builder = FeatureBuilder(use_interactions=False)
        assert builder.get_input_dim() == FEATURE_DIM

    def test_scaler_fit_transform(self):
        builder = FeatureBuilder()
        matrix = np.random.randn(100, FEATURE_DIM)
        builder.fit_scaler(matrix)
        transformed = builder.transform(matrix[0])
        assert isinstance(transformed, np.ndarray)
        assert len(transformed) == FEATURE_DIM

    def test_scaler_no_mean_returns_input(self):
        builder = FeatureBuilder()
        feats = np.ones(FEATURE_DIM)
        result = builder.transform(feats)
        np.testing.assert_array_almost_equal(result, feats)


# ─── PredictionResult Tests ───

class TestPredictionResult:
    def test_to_db_payload(self):
        result = PredictionResult(
            match_id=1,
            spf={"home": 0.5, "draw": 0.3, "away": 0.2},
            rq={"home": 0.4, "draw": 0.35, "away": 0.25, "handicap": -1},
            score={"1:0": 0.15, "0:0": 0.10},
            goals={"1": 0.3, "2": 0.25},
            half={"主主": 0.4},
            confidence="high",
            model_version="v2.0",
        )
        payload = result.to_db_payload()
        assert len(payload) >= 5

    def test_empty_result(self):
        result = PredictionResult(match_id=99)
        payload = result.to_db_payload()
        assert len(payload) == 5


# ─── Context Tests ───

class TestContext:
    def test_match_context_has_odds(self, match_ctx):
        assert match_ctx.has_odds
        assert match_ctx.has_closing_odds

    def test_match_context_no_odds(self):
        ctx = MatchContext(1,
            home_team=TeamContext(1, "A"),
            away_team=TeamContext(2, "B"))
        assert not ctx.has_odds
        assert not ctx.has_closing_odds

    def test_team_context_defaults(self):
        team = TeamContext(1, "Default")
        assert team.elo == 1500
        assert team.fifa_rank == 100
        assert team.avg_goals_scored == 1.30
        assert team.tactical_style == "balanced"
