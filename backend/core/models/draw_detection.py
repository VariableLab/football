"""平局检测修正模型 — 优先使用NN分类器，fallback到规则式校准。"""

from typing import Any, Dict, Optional

from core.context import MatchContext


class DrawDetectionModel:
    """平局检测子模型 — 优先使用NN分类器，fallback到规则式校准。"""
    _nn_predictor: Optional[Any] = None

    @classmethod
    def _get_nn_predictor(cls) -> Optional[Any]:
        if cls._nn_predictor is None:
            try:
                from core.draw_classifier import DrawClassifierPredictor
                predictor = DrawClassifierPredictor()
                if predictor.is_ready():
                    cls._nn_predictor = predictor
            except Exception:
                pass
        return cls._nn_predictor

    @classmethod
    def predict(
        cls,
        spf: Dict[str, float],
        ctx: MatchContext,
        market: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """检测平局并修正SPF概率"""
        nn = cls._get_nn_predictor()
        if nn is not None:
            home_xg = getattr(ctx.home_team, "avg_xg", 0) or ctx.home_team.avg_goals_scored
            away_xg = getattr(ctx.away_team, "avg_xg", 0) or ctx.away_team.avg_goals_scored

            draw_prob_nn = nn.predict_from_match(
                elo_diff=ctx.home_team.elo - ctx.away_team.elo,
                xg_diff=home_xg - away_xg,
                market_draw_prob=market.get("draw") if market else None,
                model_draw_prob=spf.get("draw", 0.25),
                competition="",
                venue_type=getattr(ctx, "venue_type", "neutral"),
                temperature=getattr(ctx, "temperature", 20.0),
                odds_home=ctx.closing_odds_home or ctx.odds_home,
                odds_draw=ctx.closing_odds_draw or ctx.odds_draw,
                odds_away=ctx.closing_odds_away or ctx.odds_away,
                draw_movement=0.0,
            )
            return nn.adjust_spf(spf, draw_prob_nn)

        from core.draw_calibrator import (
            DrawFeatures,
            apply_draw_calibration,
            load_draw_params,
        )
        home_xg = getattr(ctx.home_team, "avg_xg", 0) or ctx.home_team.avg_goals_scored
        away_xg = getattr(ctx.away_team, "avg_xg", 0) or ctx.away_team.avg_goals_scored
        features = DrawFeatures(
            elo_diff=ctx.home_team.elo - ctx.away_team.elo,
            xg_diff=home_xg - away_xg,
            market_draw_prob=market.get("draw") if market else None,
            is_knockout=ctx.is_knockout,
        )
        return apply_draw_calibration(spf, features, load_draw_params())
