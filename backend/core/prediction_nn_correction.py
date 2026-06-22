"""
NN 修正层 — StackingNet + BetNN 概率修正

在 LR Fusion 之后，用神经网络修正系统性偏差。
"""
from __future__ import annotations

from typing import Dict, Optional
import numpy as np


def apply_residual_correction(
    spf: Dict[str, float], ctx, poisson_out: Dict, market_out: Optional[Dict[str, float]],
    engine,
) -> Dict[str, float]:
    """使用 Stacking NN (v3) 修正融合概率"""
    try:
        from core.residual_nn import StackingPredictor
        predictor = StackingPredictor()
        if not predictor.is_ready():
            return spf

        form_features = h2h_features = None
        if engine.fusion._db is not None:
            try:
                from features.form_markov_model import FormMarkovModel
                from features.h2h_model import H2HModel
                fm = FormMarkovModel(engine.fusion._db)
                form_features = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id)
                hm = H2HModel(engine.fusion._db)
                h2h_features = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
            except Exception:
                pass

        base_feats = engine._feature_builder.build(
            elo_probs={"home": 0.33, "draw": 0.33, "away": 0.33},
            poisson_result=poisson_out,
            players_factor=1.0,
            market_probs=market_out,
            form_features=form_features,
            h2h_features=h2h_features,
            ctx=ctx,
        )

        lr_arr = np.array([spf.get('home', 0.33), spf.get('draw', 0.33), spf.get('away', 0.33)], dtype=np.float32)
        mkt_arr = np.array([market_out.get('home', 0.33), market_out.get('draw', 0.33), market_out.get('away', 0.33)] if market_out else lr_arr, dtype=np.float32)
        full_input = np.concatenate([base_feats, lr_arr, mkt_arr])

        stacking_spf = predictor.predict(full_input)
        if stacking_spf:
            final_spf = {k: 0.4 * spf[k] + 0.6 * stacking_spf[k] for k in ["home", "draw", "away"]}
            total = sum(final_spf.values())
            return {k: v / total for k, v in final_spf.items()}
    except Exception as e:
        import logging
        logging.getLogger("prediction_engine").warning(f"[StackingNN] correction failed: {e}")
    return spf


def apply_betnn_correction(
    spf: Dict[str, float], ctx, poisson_out: Dict, market_out: Optional[Dict[str, float]],
    engine, fused_spf: Dict[str, float],
) -> Dict[str, float]:
    """使用 BetNN 投注价值修正 SPF 概率"""
    try:
        from core.bet_nn import BetNetPredictor
        bet_nn = BetNetPredictor()
        if not bet_nn.is_ready():
            return fused_spf

        form_features = h2h_features = None
        if engine.fusion._db is not None:
            try:
                from features.form_markov_model import FormMarkovModel
                from features.h2h_model import H2HModel
                fm = FormMarkovModel(engine.fusion._db)
                form_features = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id)
                hm = H2HModel(engine.fusion._db)
                h2h_features = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
            except Exception:
                pass

        base_feats = engine._feature_builder.build(
            elo_probs={"home": 0.33, "draw": 0.33, "away": 0.33},
            poisson_result=poisson_out,
            players_factor=1.0,
            market_probs=market_out,
            form_features=form_features,
            h2h_features=h2h_features,
            ctx=ctx,
        )

        lr_arr = np.array([fused_spf.get('home', 0.33), fused_spf.get('draw', 0.33), fused_spf.get('away', 0.33)], dtype=np.float32)
        mkt_arr = np.array([market_out.get('home', 0.33), market_out.get('draw', 0.33), market_out.get('away', 0.33)] if market_out else lr_arr, dtype=np.float32)
        full_input = np.concatenate([base_feats, lr_arr, mkt_arr])

        bet_nn_spf = bet_nn.predict(full_input)
        if bet_nn_spf:
            bet_nn_spf = {k: max(0.001, v) for k, v in bet_nn_spf.items()}
            total = sum(bet_nn_spf.values())
            bet_nn_spf = {k: v / total for k, v in bet_nn_spf.items()}
            fused_spf = {k: 0.5 * fused_spf[k] + 0.5 * bet_nn_spf[k] for k in ["home", "draw", "away"]}
            total = sum(fused_spf.values())
            return {k: v / total for k, v in fused_spf.items()}
    except Exception as e:
        import logging
        logging.getLogger("prediction_engine").debug(f"[BetNN] correction skipped: {e}")
    return fused_spf
