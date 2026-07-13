"""
测试混合比分模型 (MixtureScoreModel) 和爆冷探测器 (UpsetDetector)。
"""
import sys
import os

import numpy as np
import pytest
from core.models.mixture_score_model import MixtureScoreModel
from core.models.upset_detector import UpsetDetector


class TestMixtureScoreModel:
    """混合比分模型单元测试"""

    def test_collapse_prob_equal_teams(self):
        """实力接近 → 崩盘概率低"""
        p = MixtureScoreModel.compute_collapse_probability(
            elo_diff=20, xg_diff=0.1,
            home_form=1.0, away_form=0.95,
            home_injuries=0, away_injuries=0,
            home_rest_days=5, away_rest_days=5,
        )
        # sigmoid 有基线 ~0.08, 但远低于高差距场景
        assert p < 0.15, f"Equal teams should have low collapse prob, got {p}"

    def test_collapse_prob_large_gap(self):
        """Elo 差距 400 → 崩盘概率高"""
        p = MixtureScoreModel.compute_collapse_probability(
            elo_diff=400, xg_diff=2.0,
            home_form=1.2, away_form=0.7,
            home_injuries=0, away_injuries=3,
            home_rest_days=7, away_rest_days=2,
        )
        assert p > 0.3, f"Large elo gap should trigger high collapse prob, got {p}"

    def test_collapse_prob_cap(self):
        """崩盘概率不能超过 0.85"""
        p = MixtureScoreModel.compute_collapse_probability(
            elo_diff=500, xg_diff=3.0,
            home_form=1.3, away_form=0.5,
            home_injuries=5, away_injuries=5,
            home_rest_days=1, away_rest_days=1,
            tactical_clash="attack_counter",
        )
        assert p <= 0.85

    def test_collapse_prob_zero_signals(self):
        """所有信号都是最低 → 崩盘概率低"""
        p = MixtureScoreModel.compute_collapse_probability(
            elo_diff=10, xg_diff=0.05,
            home_form=1.0, away_form=0.99,
            home_injuries=0, away_injuries=0,
            home_rest_days=10, away_rest_days=10,
        )
        # sigmoid 基线 ~0.08
        assert p < 0.15, f"Zero signals should have low collapse prob, got {p}"

    def test_predict_no_mixture_low_prob(self):
        """崩盘概率 < 0.05 → 直接返回标准泊松"""
        result = MixtureScoreModel.predict(lambda_h=1.5, lambda_a=0.8, collapse_prob=0.01)
        # 应该只有标准比分
        assert "1:0" in result
        assert "0:0" in result
        assert "2:1" in result

    def test_predict_mixture_high_prob(self):
        """崩盘概率高 → 应该产生更多大比分"""
        result_normal = MixtureScoreModel.predict(lambda_h=2.0, lambda_a=0.8, collapse_prob=0.0)
        result_mixed = MixtureScoreModel.predict(lambda_h=2.0, lambda_a=0.8, collapse_prob=0.5)

        # 混合模式下，大比分概率应该更高
        big_normal = sum(v for k, v in result_normal.items() if int(k.split(":")[0]) >= 3)
        big_mixed = sum(v for k, v in result_mixed.items() if int(k.split(":")[0]) >= 3)

        assert big_mixed > big_normal, "High collapse should increase big score probability"

    def test_predict_probability_sum(self):
        """所有比分概率之和应该 ≈ 1.0"""
        result = MixtureScoreModel.predict(lambda_h=2.1, lambda_a=0.8, collapse_prob=0.4)
        total = sum(result.values())
        assert abs(total - 1.0) < 0.01, f"Probabilities should sum to 1.0, got {total}"

    def test_collapse_lambdas_asymmetric(self):
        """崩盘时 λ 放大应该不对称"""
        lh_n, la_n = 2.1, 0.8
        lh_c, la_c = MixtureScoreModel.collapse_lambdas(lh_n, la_n, 0.5)

        # 强队 (home) λ 应该放大
        assert lh_c > lh_n
        # 弱队 (away) λ 应该缩小
        assert la_c < la_n

    def test_collapse_lambdas_away_strong(self):
        """如果客队更强，放大方向应该反过来"""
        lh_n, la_n = 0.8, 2.1
        lh_c, la_c = MixtureScoreModel.collapse_lambdas(lh_n, la_n, 0.5)

        assert la_c > la_n
        assert lh_c < lh_n

    def test_detect_tactical_clash(self):
        """战术相克检测"""
        # 创建一个 mock context
        class MockTeam:
            tactical_style = "attack"
            name = "Test"

        ctx = type('Ctx', (), {
            'home_team': MockTeam(),
            'away_team': type('AT', (), {'tactical_style': 'counter', 'name': 'Away'})()
        })()

        result = MixtureScoreModel._detect_tactical_clash(ctx)
        assert result == "attack_counter"

        ctx.home_team.tactical_style = "balanced"
        result = MixtureScoreModel._detect_tactical_clash(ctx)
        assert result == "none"


class TestUpsetDetector:
    """爆冷探测器单元测试"""

    def test_identical_distributions(self):
        """模型和市场完全一致 → KL 散度 ≈ 0"""
        detector = UpsetDetector()
        model = {"home": 0.5, "draw": 0.3, "away": 0.2}
        market = {"home": 0.5, "draw": 0.3, "away": 0.2}
        signal = detector.detect(model, market)

        assert signal.kl_divergence < 0.001
        assert signal.upset_probability < 0.05
        assert not signal.is_upset_candidate

    def test_high_divergence(self):
        """模型和市场严重分歧 → 高爆冷概率"""
        detector = UpsetDetector()
        model = {"home": 0.8, "draw": 0.15, "away": 0.05}
        market = {"home": 0.3, "draw": 0.3, "away": 0.4}
        signal = detector.detect(model, market)

        assert signal.kl_divergence > 0.1
        assert signal.upset_probability > 0.1
        assert signal.divergence_direction == "model_home_favor"

    def test_draw_divergence(self):
        """模型看好平局 → 正确识别方向"""
        detector = UpsetDetector()
        model = {"home": 0.25, "draw": 0.6, "away": 0.15}
        market = {"home": 0.4, "draw": 0.25, "away": 0.35}
        signal = detector.detect(model, market)

        assert signal.divergence_direction == "model_draw_favor"

    def test_odds_to_market_probs(self):
        """赔率 → 市场隐含概率计算"""
        market = UpsetDetector._odds_to_market_probs(1.8, 3.5, 4.5)

        # 应该归一化到和为 1
        total = sum(market.values())
        assert abs(total - 1.0) < 0.001

        # 最低赔率 → 最高概率
        assert market["home"] > market["draw"] > market["away"]

    def test_detect_from_odds(self):
        """便捷方法: 直接从赔率检测"""
        detector = UpsetDetector()
        model = {"home": 0.7, "draw": 0.2, "away": 0.1}
        signal = detector.detect_from_odds(
            model, odds_home=2.5, odds_draw=3.5, odds_away=3.0
        )

        # 模型强烈看好主胜 (70%), 但市场赔率 2.5 隐含 ~40%
        # 所以应该有显著分歧
        assert signal.kl_divergence > 0.0

    def test_score_divergence(self):
        """比分级别的 KL 散度检测"""
        detector = UpsetDetector()
        model_score = {"1:0": 0.15, "2:1": 0.12, "2:0": 0.10, "0:0": 0.08}
        market_score = {"1:0": 0.08, "2:1": 0.05, "2:0": 0.04, "0:0": 0.15}

        model_spf = {"home": 0.6, "draw": 0.25, "away": 0.15}
        market_spf = {"home": 0.4, "draw": 0.35, "away": 0.25}

        signal = detector.detect(model_spf, market_spf, model_score, market_score)

        # 比分分歧应该提升整体 KL 散度
        assert signal.kl_divergence > 0.0

    def test_max_upset_prob_cap(self):
        """爆冷概率有上限"""
        detector = UpsetDetector()
        # 极端分歧
        model = {"home": 0.99, "draw": 0.005, "away": 0.005}
        market = {"home": 0.01, "draw": 0.01, "away": 0.98}
        signal = detector.detect(model, market)

        assert signal.upset_probability <= 0.65


class TestPoissonModelMixtureIntegration:
    """PoissonModel 与混合模型的集成测试"""

    def test_heavy_tail_returns_collapse_prob(self):
        """predict_with_heavy_tail 在高 Elo 差距时应返回 collapse_prob"""
        from core.models.poisson import PoissonModel

        class MockTeam:
            avg_xg = 2.0; avg_goals_scored = 1.8
            avg_xga = 0.5; avg_goals_conceded = 0.6
            tournament_matches_played = 5; tournament_goals_scored = 12
            form_factor = 1.15; fifa_rank = 10; elo = 1900
            key_injuries = ''; recent_results = 'WWWDW'; tactical_style = 'attack'
            rest_days = 5; home_away_factor = 1.1
            squad_fatigue_index = 0.3; weather_adaptability = 1.0
            possession = 55; coach_rating = 75; shot_accuracy = 0.15
            crossing_rate = 0.2; set_piece_threat = 0.12

        class MockCtx:
            def __init__(self):
                self.home_team = MockTeam()
                self.away_team = MockTeam()
                self.away_team.elo = 1500
                self.away_team.form_factor = 0.80
                self.away_team.key_injuries = "p1,p2,p3"
                self.away_team.rest_days = 2
                self.away_team.tactical_style = "defense"
                self.handicap = 0
                self.is_knockout = False
                self.stage = ""
                self.is_third_round_group = False
                self.has_closing_odds = False
                self.venue_type = "home"
                self.weather = None
                self.temperature = None
                self.pitch_condition = None
                self.schedule_density = None
                self.competition = "WC"
                self.match_id = 999

        ctx = MockCtx()
        result = PoissonModel.predict_with_heavy_tail(ctx, use_heavy_tail=True)

        assert "spf" in result
        assert "score" in result
        assert "goals" in result
        assert result.get("heavy_tail") is True
        assert "collapse_prob" in result
        assert result["collapse_prob"] > 0.05

    def test_heavy_tail_disabled(self):
        """禁用重尾 → 应返回标准预测"""
        from core.models.poisson import PoissonModel

        class MT:
            avg_xg = 1.5; avg_goals_scored = 1.4
            avg_xga = 1.0; avg_goals_conceded = 1.1
            tournament_matches_played = 3; tournament_goals_scored = 5
            form_factor = 1.0; fifa_rank = 50; elo = 1600
            key_injuries = ''; recent_results = 'WDWLW'; tactical_style = 'balanced'
            rest_days = 5; home_away_factor = 1.1
            squad_fatigue_index = 0.3; weather_adaptability = 1.0
            possession = 50; coach_rating = 70; shot_accuracy = 0.12
            crossing_rate = 0.15; set_piece_threat = 0.10

        class MC:
            home_team = MT(); away_team = MT()
            away_team.elo = 1550; away_team.form_factor = 0.95
            away_team.key_injuries = ''
            away_team.rest_days = 5; away_team.tactical_style = 'balanced'
            handicap = 0; is_knockout = False; stage = ''
            is_third_round_group = False; has_closing_odds = False
            venue_type = 'home'; weather = None; temperature = None
            pitch_condition = None; schedule_density = None
            competition = ''; match_id = 1

        result = PoissonModel.predict_with_heavy_tail(MC(), use_heavy_tail=False)
        assert result.get("heavy_tail") is not True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
