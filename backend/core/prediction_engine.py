"""
世界杯预测引擎 — 主入口

整合全部子模型，对外提供统一的 predict() 接口。
核心管线: Elo → Poisson → LR Fusion → NN Correction → BetNN → Shadow/Deep → Result

用法:
    engine = PredictionEngine()
    result = engine.predict(ctx)
    # result 包含全部 6 种玩法的概率分布
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

# ─── 子模型导入 ───
from core.models import EloModel, PoissonModel, PlayerAdjustmentModel, MarketModel, DrawDetectionModel
from core.models.mixture_score_model import MixtureScoreModel
from core.models.upset_detector import UpsetDetector
from features import EloModel as FeEloModel, PoissonModel as FePoissonModel
from features.feature_builder import FeatureBuilder
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel
import fusion.logistic_fusion as _lr_module

LogisticFusionWeights = _lr_module.LogisticFusionWeights

# ─── 常量 ───
from core.constants import (
    MAX_GOALS, POISSON_TRUNCATE, HOME_ADVANTAGE_ELO, FORM_WINDOW_MATCHES,
    DIXON_COLES_RHO, DRAW_INFLATION_FACTOR, DEFAULT_WEIGHTS,
)

_ENGINE_CFG = {
    "MAX_GOALS": MAX_GOALS,
    "POISSON_TRUNCATE": POISSON_TRUNCATE,
    "HOME_ADVANTAGE_ELO": HOME_ADVANTAGE_ELO,
    "FORM_WINDOW_MATCHES": FORM_WINDOW_MATCHES,
    "DIXON_COLES_RHO": DIXON_COLES_RHO,
    "DRAW_INFLATION_FACTOR": DRAW_INFLATION_FACTOR,
    "DEFAULT_WEIGHTS": DEFAULT_WEIGHTS,
}
load_engine_config = lambda: _ENGINE_CFG

from core.context import TeamContext, MatchContext, PredictionResult
from core.prediction_fusion import EnsembleFusion
from core.prediction_nn_correction import apply_residual_correction, apply_betnn_correction
from core.prediction_recalibration import recalibrate_scores, recalibrate_goals, recalibrate_half
from core.prediction_confidence import compute_confidence


# ────────────────────────────
# 主预测引擎
# ────────────────────────────
class PredictionEngine:
    """整合全部子模型，对外提供统一的 predict() 接口。"""

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None,
                 use_lr_fusion: bool = True):
        self.db = db_session
        self.fusion = EnsembleFusion(weights, db_session=db_session)
        self.use_lr_fusion = use_lr_fusion
        self._lr_weights_cache: Dict[str, LogisticFusionWeights] = {}
        self._feature_builder = FeatureBuilder(use_interactions=True)
        if use_lr_fusion:
            global_w = self._load_lr_weights("global")
            if global_w:
                self._lr_weights_cache["global"] = global_w

    @property
    def _lr_weights(self) -> Optional[LogisticFusionWeights]:
        return self._lr_weights_cache.get("global")

    @staticmethod
    def _load_lr_weights(league: str = "global") -> Optional[LogisticFusionWeights]:
        import glob
        try:
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _lr_weights_dir = os.path.join(_root, "data", "weights", "lr")
            pattern = os.path.join(_lr_weights_dir, f"{league}_v1_*.json")
            lr_files = sorted(glob.glob(pattern))
            if lr_files:
                w = LogisticFusionWeights.load(lr_files[-1])
                import logging
                logging.getLogger("prediction_engine").info(
                    f"[LR-fusion] Loaded {league} weights: {os.path.basename(lr_files[-1])} "
                    f"(acc={w.accuracy:.1%}, n={w.sample_count})"
                )
                return w
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[LR-fusion] Failed to load {league} weights: {e}")
        return None

    def _get_lr_weights_for_match(self, competition: str) -> Optional[LogisticFusionWeights]:
        if competition in self._lr_weights_cache:
            return self._lr_weights_cache[competition]
        w = self._load_lr_weights(competition)
        if w:
            self._lr_weights_cache[competition] = w
            return w
        return self._lr_weights_cache.get("global")

    def predict(self, ctx: MatchContext) -> PredictionResult:
        from core.logic_tracer import LogicChain
        trace = LogicChain(match_id=ctx.match_id)

        # ─── F1 Bridge: Lab Expert Models ───
        lab_poisson_spf = None
        lab_elo_spf = None
        try:
            _cur_dir = os.path.dirname(os.path.abspath(__file__))
            _b_root = os.path.dirname(_cur_dir)
            try:
                from core.research_poisson import PoissonPredictor as LabPoisson
                from core.research_elo import EloPredictor as LabElo
            except ImportError:
                from research_poisson import PoissonPredictor as LabPoisson
                from research_elo import EloPredictor as LabElo

            p_weight_path = os.path.join(_b_root, "data", "weights", "research", "poisson_expert_weights.json")
            e_weight_path = os.path.join(_b_root, "data", "weights", "research", "elo_expert_weights.json")

            if os.path.exists(p_weight_path):
                if not hasattr(PredictionEngine, "_lab_poisson_cache"):
                    PredictionEngine._lab_poisson_cache = LabPoisson()
                    PredictionEngine._lab_poisson_cache.load_params(p_weight_path)
                lab_p = PredictionEngine._lab_poisson_cache
                h_name = ctx.home_team.name_en or ctx.home_team.name
                a_name = ctx.away_team.name_en or ctx.away_team.name
                df_mock = pd.DataFrame([{"HomeTeam": h_name, "AwayTeam": a_name}])
                lab_p_res = lab_p.predict_proba(df_mock)
                if lab_p_res is not None and len(lab_p_res) > 0 and lab_p_res[0] is not None:
                    lab_poisson_spf = {"home": lab_p_res[0][0], "draw": lab_p_res[0][1], "away": lab_p_res[0][2]}
                    trace.add_step("Lab-Expert Poisson", "使用实验室 Dixon-Coles 泊松参数", lab_poisson_spf)

            if os.path.exists(e_weight_path):
                if not hasattr(PredictionEngine, "_lab_elo_cache"):
                    PredictionEngine._lab_elo_cache = LabElo()
                    PredictionEngine._lab_elo_cache.load_params(e_weight_path)
                lab_e = PredictionEngine._lab_elo_cache
                h_name = ctx.home_team.name_en or ctx.home_team.name
                a_name = ctx.away_team.name_en or ctx.away_team.name
                df_mock = pd.DataFrame([{"HomeTeam": h_name, "AwayTeam": a_name}])
                lab_e_res = lab_e.predict_proba(df_mock)
                if lab_e_res is not None and len(lab_e_res) > 0 and lab_e_res[0] is not None:
                    lab_elo_spf = {"home": lab_e_res[0][0], "draw": lab_e_res[0][1], "away": lab_e_res[0][2]}
                    trace.add_step("Lab-Expert Elo", "使用实验室百年历史基准 Elo 参数", lab_elo_spf)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[F1-Bridge] Lab injection failed: {e}")

        # ─── Step 1: Sub-models ───
        elo_out = EloModel.predict(ctx)
        if lab_elo_spf:
            # Blend Lab Elo as supplementary signal (30%) rather than full override
            elo_out = {k: 0.30 * lab_elo_spf[k] + 0.70 * elo_out[k] for k in ["home", "draw", "away"]}

        elo_gap = abs(ctx.home_team.elo - ctx.away_team.elo)
        use_heavy_tail = elo_gap > 200
        poisson_out = PoissonModel.predict_with_heavy_tail(ctx, use_heavy_tail=use_heavy_tail)
        if lab_poisson_spf:
            # Blend Lab Poisson as supplementary signal (30%) rather than full override
            poisson_out["spf"] = {k: 0.30 * lab_poisson_spf[k] + 0.70 * poisson_out["spf"][k] for k in ["home", "draw", "away"]}

        players_factor = PlayerAdjustmentModel.predict(ctx)
        market_out = MarketModel.predict(ctx)

        is_degraded = False
        _odds_source = getattr(ctx, 'odds_source', getattr(ctx.odds, 'source', '')) if hasattr(ctx, 'odds') and ctx.odds else getattr(ctx, 'odds_source', '')
        if _odds_source == "synthetic" or market_out is None:
            is_degraded = True
            trace.add_step("数据断流预警", "检测到赔率数据源失效，系统自动进入 [反脆弱降级模式]", {"mode": "degraded", "source": _odds_source})

        # ─── Step 2: Fusion ───
        lr_spf = None
        weights = self._get_lr_weights_for_match(ctx.competition)
        real_market = market_out if not is_degraded else None

        if weights and real_market is not None:
            lr_spf = self._predict_with_lr(ctx, elo_out, poisson_out, players_factor, real_market, weights)

        if lr_spf is not None:
            fused_spf = lr_spf
            trace.add_step("逻辑回归基准", f"使用 {ctx.competition or '全球'} 48维特征模型", fused_spf)
            if real_market:
                fused_spf = {k: 0.5 * fused_spf[k] + 0.5 * real_market[k] for k in ["home", "draw", "away"]}
                trace.add_step("市场共识校准", "模型与机构赔率 50:50 融合", fused_spf)
        else:
            fused_spf = self.fusion.fuse_spf(elo=elo_out, poisson=poisson_out["spf"],
                players=players_factor, market=real_market, ctx=ctx)
            mode_desc = "基础混合融合" if not is_degraded else "纯物理实力降级融合"
            trace.add_step(mode_desc, "使用 Elo + 泊松 4 参数模型", fused_spf)

        # Lab Elo expert override (supplementary, not dominant)
        if lab_elo_spf:
            weight_factor = 0.50 if is_degraded else 0.35
            fused_spf = {k: weight_factor * lab_elo_spf[k] + (1-weight_factor) * fused_spf[k] for k in ["home", "draw", "away"]}
            s_val = sum(fused_spf.values())
            fused_spf = {k: v / s_val for k, v in fused_spf.items()}
            trace.add_step("专家Elo降级增强", f"权重调整为 {weight_factor:.0%} (补充信号而非主导)", fused_spf)

        # Live odds steam move
        old_spf = fused_spf.copy()
        fused_spf = self._apply_live_odds_override(fused_spf, ctx)
        if fused_spf != old_spf:
            trace.add_step("临场异动修正", "赔率剧烈跳水，强制对齐资金流向", fused_spf)

        # Draw detection
        fused_spf = DrawDetectionModel.predict(fused_spf, ctx, market_out)
        trace.add_step("平局概率微调", "Draw-MLP 分类器偏置修正", fused_spf)

        # ─── Step 2d/2e: NN Corrections ───
        if lr_spf is not None:
            fused_spf = apply_residual_correction(fused_spf, ctx, poisson_out, market_out, self)
            trace.add_step("利润导向 NN 修正", "StackingNet 残差学习", fused_spf)

            fused_spf = apply_betnn_correction(fused_spf, ctx, poisson_out, market_out, self, fused_spf)
            trace.add_step("BetNN 投注价值修正", "BetNet 二次校准 SPF 概率", fused_spf)

        # ─── Step 3: RQ ───
        rq_raw = poisson_out["rq"].copy()
        spf_direction = fused_spf["home"] - fused_spf["away"]
        rq_direction = rq_raw["home"] - rq_raw["away"]
        if spf_direction * rq_direction < 0:
            rq_raw["home"] = (rq_raw["home"] + fused_spf["home"]) / 2
            rq_raw["away"] = (rq_raw["away"] + fused_spf["away"]) / 2
            rq_raw["draw"] = 1 - rq_raw["home"] - rq_raw["away"]
        rq = {k: max(0.001, v) for k, v in rq_raw.items() if k != "handicap"}
        total = sum(rq.values())
        rq = {k: v / total for k, v in rq.items()}

        # ─── Step 4: Score/Goals/Half ───
        score = recalibrate_scores(poisson_out["score"], fused_spf, use_heavy_tail)
        goals = recalibrate_goals(score)
        half = recalibrate_half(poisson_out["half"], fused_spf)

        # ─── Step 5: Confidence ───
        confidence = compute_confidence(fused_spf, market_out, ctx)

        is_mock_data = ctx.home_team.elo == 1600 or ctx.away_team.elo == 1600
        if is_mock_data:
            confidence = "low"

        # ─── Shadow / Deep Frontier ───
        shadow_data = classic_data = deep_data = None
        try:
            from core.shadow_engine import ShadowPredictor
            real_handicap = getattr(ctx, "handicap", 0) or 0
            shadow_data = ShadowPredictor.predict(ctx, real_handicap, target_spf=fused_spf)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Shadow] Predict failed: {e}")

        try:
            from core.shadow_engine import ShadowPredictor
            real_handicap = getattr(ctx, "handicap", 0) or 0
            classic_data = ShadowPredictor.predict(ctx, real_handicap, target_spf=None)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Classic] Predict failed: {e}")

        try:
            from core.deep_frontier_nn import DeepFrontierPredictor
            df_predictor = DeepFrontierPredictor(db_session=self.db)
            if df_predictor.is_ready():
                from features.feature_builder import FeatureBuilder
                temp_builder = FeatureBuilder(use_interactions=False)
                static_feats = temp_builder.build(elo_probs=elo_out, poisson_result=poisson_out,
                    players_factor=players_factor, market_probs=market_out, form_features=None,
                    h2h_features=None, ctx=ctx)
                lam_h_pred, lam_a_pred = df_predictor.predict_xg(self.db, ctx, static_feats[:48])
                deep_data = ShadowPredictor.predict(ctx, real_handicap=0, target_spf=None, custom_lambdas=(lam_h_pred, lam_a_pred))
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Deep Frontier] Predict failed: {e}")

        # ─── Mixture Signals ───
        mixture_signals = None
        try:
            collapse_prob = MixtureScoreModel.compute_collapse_probability(
                elo_diff=ctx.home_team.elo - ctx.away_team.elo,
                xg_diff=((ctx.home_team.avg_xg or ctx.home_team.avg_goals_scored) - (ctx.away_team.avg_xg or ctx.away_team.avg_goals_conceded)),
                home_form=ctx.home_team.form_factor, away_form=ctx.away_team.form_factor,
                home_injuries=len([x for x in (ctx.home_team.key_injuries or "").split(",") if x.strip()]),
                away_injuries=len([x for x in (ctx.away_team.key_injuries or "").split(",") if x.strip()]),
                home_rest_days=getattr(ctx.home_team, "rest_days", 7),
                away_rest_days=getattr(ctx.away_team, "rest_days", 7),
            )
            upset_signal = None
            if poisson_out and "spf" in poisson_out:
                signal = UpsetDetector.detect_from_odds(
                    model_spf=poisson_out["spf"],
                    odds_home=getattr(ctx, "odds_home", 2.0) or 2.0,
                    odds_draw=getattr(ctx, "odds_draw", 3.2) or 3.2,
                    odds_away=getattr(ctx, "odds_away", 3.5) or 3.5,
                )
                upset_signal = {
                    "kl_divergence": signal.kl_divergence,
                    "upset_probability": signal.upset_probability,
                    "divergence_direction": signal.divergence_direction,
                    "is_upset_candidate": signal.is_upset_candidate,
                    "confidence": signal.confidence,
                }
            mixture_signals = {
                "collapse_prob": round(collapse_prob, 4),
                "big_score_warning": collapse_prob > 0.25,
                "upset_signal": upset_signal,
            }
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").debug(f"Mixture signals failed: {e}")

        return PredictionResult(
            match_id=ctx.match_id, spf=fused_spf, rq=rq, score=score, goals=goals, half=half,
            raw_elo=elo_out, raw_poisson=poisson_out["spf"], raw_players=players_factor,
            raw_market=market_out or {}, model_version="v2.0", confidence=confidence,
            odds_degraded=market_out is None or is_mock_data,
            weights_used={"_fusion": "lr_v2", **(lr_spf or {})} if lr_spf is not None else self.fusion.get_effective_weights(market_out, ctx),
            trace=trace, shadow_data=shadow_data, classic_data=classic_data,
            deep_data=deep_data, mixture_signals=mixture_signals,
        )

    def _predict_with_lr(self, ctx, elo_out, poisson_out, players_factor, market_out, weights):
        """使用 LR 逻辑回归融合预测 SPF"""
        try:
            form_features = h2h_features = None
            if self.fusion._db is not None:
                try:
                    fm = FormMarkovModel(self.fusion._db)
                    form_features = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id, is_home=True)
                    hm = H2HModel(self.fusion._db)
                    h2h_features = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
                except Exception:
                    pass
            features = self._feature_builder.build(elo_probs=elo_out, poisson_result=poisson_out,
                players_factor=players_factor, market_probs=market_out, form_features=form_features,
                h2h_features=h2h_features, ctx=ctx)
            return weights.predict(features)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[LR-fusion] predict failed: {e}")
            return None

    def _apply_live_odds_override(self, spf, ctx):
        """动态市场异动修正 (Steam Move Adjustment)"""
        if not ctx.has_closing_odds or not ctx.has_odds:
            return spf
        moves = {}
        for sel in ["home", "draw", "away"]:
            closing = getattr(ctx, f"closing_odds_{sel}")
            opening = getattr(ctx, f"odds_{sel}")
            moves[sel] = (closing - opening) / opening if closing and opening else 0.0
        best_move_sel = min(moves, key=moves.get)
        best_move_val = moves[best_move_sel]
        intensity = 1.0 / (1.0 + np.exp(-20 * (abs(best_move_val) - 0.10)))
        if abs(best_move_val) > 0.05 and best_move_val < 0:
            target_prob = 0.65 if intensity > 0.5 else 0.50
            alpha = 0.4 * intensity
            spf[best_move_sel] = (1 - alpha) * spf[best_move_sel] + alpha * target_prob
            total = sum(spf.values())
            spf = {k: v / total for k, v in spf.items()}
        return spf


# ────────────────────────────
# 回测框架
# ────────────────────────────
@dataclass
class BacktestResult:
    total_matches: int
    direction_accuracy: float
    high_conf_accuracy: float
    brier_score: float
    log_loss: float
    avg_max_prob: float
    weights: Dict[str, float]


def brier_score(prob_true: float, outcome: int) -> float:
    return (prob_true - outcome) ** 2


def direction_correct(pred: Dict[str, float], actual: str) -> bool:
    return max(pred, key=pred.get) == actual


class Backtester:
    def __init__(self, engine: PredictionEngine):
        self.engine = engine

    def evaluate_single(self, ctx: MatchContext, actual_outcome: str) -> Dict[str, float]:
        result = self.engine.predict(ctx)
        spf = result.spf
        correct = direction_correct(spf, actual_outcome)
        bs = sum(brier_score(spf[k], 1 if actual_outcome == k else 0) for k in ["home", "draw", "away"]) / 3.0
        ll = -math.log(max(spf.get(actual_outcome, 1e-6), 1e-6))
        return {"correct": float(correct), "brier": bs, "log_loss": ll, "max_prob": max(spf.values()), "confidence": result.confidence}

    def run(self, historical_matches: List[Tuple[MatchContext, str]], weight_grids: Optional[Dict[str, List[float]]] = None) -> BacktestResult:
        if weight_grids is None:
            weight_grids = {"elo": [0.1, 0.2, 0.3, 0.4, 0.5], "poisson": [0.2, 0.3, 0.4, 0.5, 0.6], "market": [0.0, 0.1, 0.2, 0.3, 0.4]}
        best_score = float("inf")
        best_weights = DEFAULT_WEIGHTS.copy()
        best_metrics = {}
        for w_elo in weight_grids["elo"]:
            for w_poisson in weight_grids["poisson"]:
                for w_market in weight_grids["market"]:
                    w_players = 1.0 - w_elo - w_poisson - w_market
                    if w_players < 0 or w_players > 0.5:
                        continue
                    weights = {"elo": w_elo, "poisson": w_poisson, "players": w_players, "market": w_market}
                    metrics = self._evaluate_weights(historical_matches, weights)
                    score = metrics["avg_brier"]
                    if score < best_score:
                        best_score = score
                        best_weights = weights.copy()
                        best_metrics = metrics
        return BacktestResult(total_matches=len(historical_matches), direction_accuracy=best_metrics["accuracy"],
            high_conf_accuracy=best_metrics["high_conf_accuracy"], brier_score=best_metrics["avg_brier"],
            log_loss=best_metrics["avg_log_loss"], avg_max_prob=best_metrics["avg_max_prob"], weights=best_weights)

    def _evaluate_weights(self, matches, weights):
        engine = PredictionEngine(weights=weights)
        results = []
        for ctx, actual in matches:
            try:
                r = engine.predict(ctx)
                spf = r.spf
                correct = direction_correct(spf, actual)
                bs = sum(brier_score(spf[k], 1 if actual == k else 0) for k in ["home", "draw", "away"]) / 3.0
                ll = -math.log(max(spf.get(actual, 1e-6), 1e-6))
                results.append({"correct": correct, "brier": bs, "log_loss": ll, "max_prob": max(spf.values()), "confidence": r.confidence})
            except Exception:
                continue
        if not results:
            return {"accuracy": 0, "avg_brier": 1.0, "avg_log_loss": 10, "avg_max_prob": 0, "high_conf_accuracy": 0}
        corrects = [r["correct"] for r in results]
        high_conf = [r for r in results if r["confidence"] == "high"]
        return {
            "accuracy": sum(corrects) / len(corrects),
            "avg_brier": sum(r["brier"] for r in results) / len(results),
            "avg_log_loss": sum(r["log_loss"] for r in results) / len(results),
            "avg_max_prob": sum(r["max_prob"] for r in results) / len(results),
            "high_conf_accuracy": sum(r["correct"] for r in high_conf) / len(high_conf) if high_conf else 0,
        }


# ────────────────────────────
# ORM → Context 构建
# ────────────────────────────
def build_team_context_from_orm(team) -> TeamContext:
    tactical = team.tactical_style or "balanced"
    if team.possession and team.possession > 55 and tactical == "balanced":
        tactical = "attack"
    elif team.possession and team.possession < 45 and tactical == "balanced":
        tactical = "counter"
    return TeamContext(
        team_id=team.id, name=team.name, name_en=team.name_en or "",
        elo=team.elo or 1500, fifa_rank=team.fifa_rank or 100,
        avg_goals_scored=team.avg_goals_scored or 1.3, avg_goals_conceded=team.avg_goals_conceded or 1.3,
        avg_xg=team.avg_xg or team.avg_goals_scored or 0.0, avg_xga=team.avg_xga or team.avg_goals_conceded or 0.0,
        possession=team.possession or 0.0, pass_completion=team.pass_completion or 0.0,
        shots_per_game=team.shots_per_game or 0.0, form_factor=team.form_factor or 1.0,
        recent_results=team.recent_results or "",
        recent_goals_scored=team.recent_goals_scored or 0.0, recent_goals_conceded=team.recent_goals_conceded or 0.0,
        home_away_factor=team.home_away_factor or 1.0, weather_adaptability=team.weather_adaptability or 1.0,
        tactical_style=tactical, coach_rating=team.coach_rating or 0.5,
        squad_fatigue_index=team.squad_fatigue_index or 0.5,
    )


def build_context_from_match(match, handicap: int = 0) -> MatchContext:
    home = build_team_context_from_orm(match.home_team)
    away = build_team_context_from_orm(match.away_team)
    is_late = match.kickoff_at.month in (5, 6) if match.kickoff_at else False
    return MatchContext(
        match_id=match.id, home_team=home, away_team=away, kickoff_at=match.kickoff_at,
        stage=match.stage or "group", is_knockout=match.stage in ("R32", "R16", "QF", "SF", "F"),
        is_late_season=is_late, handicap=handicap, odds_home=match.odds_home,
        odds_draw=match.odds_draw, odds_away=match.odds_away,
        closing_odds_home=match.closing_odds_home, closing_odds_draw=match.closing_odds_draw,
        closing_odds_away=match.closing_odds_away, venue_type=match.venue_type or "neutral",
        weather=match.weather or "clear", temperature=match.temperature or 20.0,
        pitch_condition=match.pitch_condition or "good",
        schedule_density=match.schedule_density or "normal", competition=match.competition or "",
    )


# ────────────────────────────
# Mock 数据生成
# ────────────────────────────
def create_mock_context(
    match_id: int = 1, home_elo: int = 1985, away_elo: int = 1920,
    home_rank: int = 1, away_rank: int = 5,
    odds_home: float = 1.72, odds_draw: float = 3.40, odds_away: float = 4.80,
    stage: str = "group", is_knockout: bool = False,
) -> MatchContext:
    home = TeamContext(team_id=1, name="阿根廷", elo=home_elo, fifa_rank=home_rank,
        avg_goals_scored=1.80, avg_goals_conceded=0.70, form_factor=1.10,
        key_players_available=11, squad_fatigue_index=0.30)
    away = TeamContext(team_id=2, name="巴西", elo=away_elo, fifa_rank=away_rank,
        avg_goals_scored=1.60, avg_goals_conceded=0.90, form_factor=1.05,
        key_players_available=10, squad_fatigue_index=0.40)
    return MatchContext(match_id=match_id, home_team=home, away_team=away,
        stage=stage, is_knockout=is_knockout, odds_home=odds_home,
        odds_draw=odds_draw, odds_away=odds_away)
