"""Tests for ShadowPredictor and physical alignment."""
import pytest
from unittest.mock import patch, MagicMock
from core.shadow_engine import ShadowPredictor
from core.prediction_engine import PredictionEngine, PredictionResult

def make_mock_team(name="TeamA", elo=1500):
    t = MagicMock()
    t.team_id = 1
    t.name = name
    t.name_en = name
    t.elo = elo
    t.avg_goals_scored = 1.3
    t.avg_goals_conceded = 1.1
    t.avg_xg = 1.4
    t.avg_xga = 1.1
    t.possession = 50.0
    t.pass_completion = 0.0
    t.shots_per_game = 0.0
    t.form_factor = 1.0
    t.fifa_rank = 50
    t.key_players_available = 11
    t.key_players_total = 11
    t.key_injuries = ""
    t.squad_fatigue_index = 0.5
    t.tournament_matches_played = 0
    t.tournament_goals_scored = 0
    t.tournament_goals_conceded = 0
    t.recent_results = ""
    t.recent_goals_scored = 0.0
    t.recent_goals_conceded = 0.0
    t.home_away_factor = 1.0
    t.weather_adaptability = 1.0
    t.tactical_style = "balanced"
    t.coach_rating = 0.5
    t.rest_days = 7
    return t

def make_mock_ctx(handicap=0):
    ctx = MagicMock()
    ctx.match_id = 101
    ctx.home_team = make_mock_team("Germany", elo=1800)
    ctx.away_team = make_mock_team("Sweden", elo=1700)
    ctx.stage = "group"
    ctx.is_knockout = False
    ctx.handicap = handicap
    ctx.odds_home = 1.8
    ctx.odds_draw = 3.4
    ctx.odds_away = 4.2
    ctx.overround = 0.05
    ctx.source_count = 3
    ctx.has_closing_odds = False
    ctx.has_odds = True
    ctx.venue_type = "neutral"
    ctx.is_third_round_group = False
    ctx.referee = None
    return ctx

class TestShadowPredictor:
    """Validate physical consistency and probability summation of ShadowPredictor."""

    def test_probability_sums_to_one(self):
        ctx = make_mock_ctx()
        res = ShadowPredictor.predict(ctx, real_handicap=-1)
        
        # 1. SPF 概率求和为 1
        assert sum(res["spf"].values()) == pytest.approx(1.0, abs=1e-3)
        
        # 2. Let-Ball (RQ) 概率求和为 1
        assert sum(res["rq"].values()) - res["rq"]["handicap"] == pytest.approx(1.0, abs=1e-3)
        assert res["rq"]["handicap"] == -1

        # 3. 半全场概率求和为 1
        assert sum(res["half"].values()) == pytest.approx(1.0, abs=1e-3)
        assert len(res["half"]) == 9

        # 4. 比分概率矩阵归一化检验 (比分和应该小于等于 1，因为长尾过滤)
        assert sum(res["score"].values()) <= 1.0
        assert sum(res["score"].values()) > 0.85 # 大部分高概率比分在内

    def test_different_handicaps(self):
        ctx = make_mock_ctx()
        res_h0 = ShadowPredictor.predict(ctx, real_handicap=0)
        res_h_neg1 = ShadowPredictor.predict(ctx, real_handicap=-1)
        res_h_pos1 = ShadowPredictor.predict(ctx, real_handicap=1)

        # 在让球数更苛刻时，让胜的概率应当下降
        assert res_h_neg1["rq"]["home"] > res_h0["rq"]["home"] > res_h_pos1["rq"]["home"]
        assert res_h_neg1["rq"]["away"] < res_h0["rq"]["away"] < res_h_pos1["rq"]["away"]

    def test_prediction_result_db_payload_integration(self):
        # 验证 PredictionResult 能够将 shadow_data 正确包含入 to_db_payload()
        shadow_mock = {
            "spf": {"home": 0.6, "draw": 0.2, "away": 0.2},
            "rq": {"home": 0.4, "draw": 0.3, "away": 0.3, "handicap": -1},
            "score": {"1:0": 0.15},
            "goals": {"1": 0.25},
            "half": {"主主": 0.35}
        }
        res = PredictionResult(
            match_id=99,
            spf={"home": 0.5, "draw": 0.3, "away": 0.2},
            rq={"home": 0.3, "draw": 0.3, "away": 0.4},
            shadow_data=shadow_mock
        )
        payload = res.to_db_payload()
        
        # 应当包含 5 个默认 v2.0 记录，以及 5 个 v3.0 记录
        assert len(payload) == 10
        
        shadow_records = [r for r in payload if r["model_version"] == "v3.0"]
        assert len(shadow_records) == 5
        
        # 验证各类型能对应上
        play_types = [r["play_type"] for r in shadow_records]
        assert len(set(play_types)) == 5

    def test_ipfp_marginal_alignment(self):
        ctx = make_mock_ctx()
        target_spf = {"home": 0.55, "draw": 0.25, "away": 0.20}
        res = ShadowPredictor.predict(ctx, real_handicap=-1, target_spf=target_spf)
        
        # 验证输出的 spf 是否和传入的 target_spf 严格一致
        assert res["spf"]["home"] == pytest.approx(0.55, abs=1e-4)
        assert res["spf"]["draw"] == pytest.approx(0.25, abs=1e-4)
        assert res["spf"]["away"] == pytest.approx(0.20, abs=1e-4)
        
        # 再次验证衍生玩法概率归一
        assert sum(res["spf"].values()) == pytest.approx(1.0, abs=1e-3)
        assert sum(res["rq"].values()) - res["rq"]["handicap"] == pytest.approx(1.0, abs=1e-3)
        assert sum(res["half"].values()) == pytest.approx(1.0, abs=1e-3)
