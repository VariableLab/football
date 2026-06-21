"""
融合权重历史回归学习器

从数据库中已结束的比赛 + 预测快照，回归学习最优融合权重。
替代暴力网格搜索，支持按赛事阶段、实力差距等维度学习动态权重。

优化目标可选：
- brier : Brier Score（概率校准度，默认）
- log_loss : Log Loss
- accuracy : 方向准确率（非凸，不推荐）

用法:
from weight_learner import WeightLearner
learner = WeightLearner(db)
result = learner.learn_all(metric="brier")
# result = {"all/all": {weights}, "group/all": {weights}, ...}
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from scipy.optimize import OptimizeResult
from sqlalchemy.orm import Session

from database.models import Match, Prediction, FusionWeight, PlayType, MatchStatus, Team, MatchAIReport
from sqlalchemy.orm import joinedload
from prediction_engine import (
    PredictionEngine,
    MatchContext,
    TeamContext,
    EloModel,
    PoissonModel,
    PlayerAdjustmentModel,
    MarketModel,
    DrawDetectionModel,
    DEFAULT_WEIGHTS,
    brier_score,
    direction_correct,
)
from utils.logger import get_logger

logger = get_logger("weight_learner")


# ────────────────────────────
# 常量
# ────────────────────────────

@dataclass
class LearnedWeights:
    stage: str
    elo_diff_range: str
    weights: Dict[str, float]
    metric: str
    metric_value: float
    sample_size: int


def _get_weight_bounds():
    """返回优化变量的边界 (elo, poisson, players, market)"""
    return [(0.0, 0.6), (0.1, 0.8), (0.05, 0.6), (0.0, 0.5)]


def _weights_to_dict(w: np.ndarray) -> Dict[str, float]:
    """将数组转为权重字典，并归一化"""
    keys = ["elo", "poisson", "players", "market"]
    total = float(w.sum())
    if total <= 0:
        return DEFAULT_WEIGHTS.copy()
    return {k: max(0.0, float(v) / total) for k, v in zip(keys, w)}


def _dict_to_weights(d: Dict[str, float]) -> np.ndarray:
    """权重字典转数组"""
    return np.array([
        d.get("elo", 0.1),
        d.get("poisson", 0.6),
        d.get("players", 0.3),
        d.get("market", 0.0),
    ], dtype=float)


def _elo_diff_tier(elo_home: int, elo_away: int) -> str:
    diff = abs(elo_home - elo_away)
    if diff < 100:
        return "0-100"
    elif diff < 200:
        return "100-200"
    elif diff < 400:
        return "200-400"
    return "400+"


# ────────────────────────────
# 预计算子模型输出
# ────────────────────────────

@dataclass
class PrecomputedSample:
    """单个样本的预计算子模型输出，避免重复推理"""
    elo_home: float
    elo_draw: float
    elo_away: float
    poisson_home: float
    poisson_draw: float
    poisson_away: float
    players_factor: float
    market_home: float
    market_draw: float
    market_away: float
    has_market: bool
    actual: str  # "home" / "draw" / "away"


def _fuse_spf_fast(
    elo: np.ndarray,
    poisson: np.ndarray,
    players: np.ndarray,
    market: np.ndarray,
    has_market: np.ndarray,
    w: np.ndarray,
) -> np.ndarray:
    """向量化融合：输入 shape=(N,3)，输出 shape=(N,3)"""
    w_norm = w / max(w.sum(), 1e-8)

    # players 修正
    adjust_strength = min(1.0, w_norm[2] * 3.0)
    blend_factor = 1.0 + (players - 1.0) * adjust_strength

    # elo 调整
    elo_adj = elo.copy()
    elo_adj[:, 0] = elo[:, 0] * blend_factor
    elo_adj[:, 2] = elo[:, 2] / np.maximum(blend_factor, 0.01)
    t = elo_adj.sum(axis=1, keepdims=True)
    elo_adj = elo_adj / np.maximum(t, 1e-6)

    # poisson 调整
    poi_adj = poisson.copy()
    poi_adj[:, 0] = poisson[:, 0] * blend_factor
    poi_adj[:, 2] = poisson[:, 2] / np.maximum(blend_factor, 0.01)
    t = poi_adj.sum(axis=1, keepdims=True)
    poi_adj = poi_adj / np.maximum(t, 1e-6)

    # 加权融合
    result = (
        w_norm[0] * elo_adj
        + w_norm[1] * poi_adj
        + w_norm[3] * (market * has_market[:, None])
    )

    # 无 market 的行：用 elo+poisson 重新分配 market 权重
    no_market_mask = ~has_market
    if no_market_mask.any():
        sub_total = w_norm[0] + w_norm[1]
        if sub_total > 1e-8:
            scale = (sub_total + w_norm[3]) / sub_total
            result[no_market_mask] = (
                w_norm[0] * scale * elo_adj[no_market_mask]
                + w_norm[1] * scale * poi_adj[no_market_mask]
            )

    # 归一化
    result = np.maximum(result, 0.001)
    t = result.sum(axis=1, keepdims=True)
    result = result / t

    return result


def _objective_fast(
    w_array: np.ndarray,
    elo: np.ndarray,
    poisson: np.ndarray,
    players: np.ndarray,
    market: np.ndarray,
    has_market: np.ndarray,
    actual_idx: np.ndarray,
    metric: str,
) -> float:
    """向量化目标函数：给定权重，计算在全部样本上的损失"""
    fused = _fuse_spf_fast(elo, poisson, players, market, has_market, w_array)

    n = fused.shape[0]
    if metric == "accuracy":
        preds = np.argmax(fused, axis=1)
        correct = (preds == actual_idx).astype(float)
        return 1.0 - correct.mean()
    elif metric == "log_loss":
        probs = fused[np.arange(n), actual_idx]
        return -np.log(np.maximum(probs, 1e-6)).mean()
    else:
        one_hot = np.zeros_like(fused)
        one_hot[np.arange(n), actual_idx] = 1.0
        bs = np.mean((fused - one_hot) ** 2)
        return float(bs)


class WeightLearner:
    """从历史比赛数据回归学习融合权重"""

    def __init__(self, db: Session):
        self.db = db

    def fetch_training_data(
        self,
        stage: Optional[str] = None,
        elo_diff_range: Optional[str] = None,
        min_matches: int = 10,
    ) -> List[Tuple[MatchContext, str]]:
        """
        从数据库获取训练数据：(MatchContext, actual_outcome)。
        条件：比赛已结束，且有 Prediction 快照。
        使用子查询避免 N+1 问题。
        """
        # 先获取有 SPF 预测的 match_id 集合
        spf_match_ids = set(
            mid for (mid,) in self.db.query(Prediction.match_id)
            .filter(Prediction.play_type == PlayType.SPF)
            .all()
        )

        query = (
            self.db.query(Match)
            .options(joinedload(Match.home_team), joinedload(Match.away_team))
            .filter(Match.status == MatchStatus.FINISHED)
            .filter(Match.actual_outcome.isnot(None))
            .filter(Match.actual_home_goals.isnot(None))
            .filter(Match.actual_away_goals.isnot(None))
            .filter(Match.id.in_(spf_match_ids))
            .order_by(Match.kickoff_at.asc())  # 确保按时间线从旧到新排序，防止时序泄露
        )
        if stage and stage != "all":
            query = query.filter(Match.stage == stage)

        matches = query.all()

        # 时序状态追踪器
        elo_tracker = {}       # {team_id: elo}
        results_tracker = {}   # {team_id: recent_results_str}

        def _get_elo(tid: int) -> int:
            return elo_tracker.get(tid, 1500)

        def _get_recent(tid: int) -> str:
            return results_tracker.get(tid, "")

        def _expected_score(r_a: float, r_b: float) -> float:
            return 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))

        def _update_trackers(h_id: int, a_id: int, outcome: str):
            curr_h_elo = _get_elo(h_id)
            curr_a_elo = _get_elo(a_id)

            if outcome == "home":
                s_h, s_a = 1.0, 0.0
                char_h, char_a = "W", "L"
            elif outcome == "away":
                s_h, s_a = 0.0, 1.0
                char_h, char_a = "L", "W"
            else:  # draw
                s_h, s_a = 0.5, 0.5
                char_h, char_a = "D", "D"

            e_h = _expected_score(curr_h_elo, curr_a_elo)
            e_a = 1.0 - e_h

            # 更新 Elo Rating (K-factor = 32)
            elo_tracker[h_id] = round(curr_h_elo + 32.0 * (s_h - e_h))
            elo_tracker[a_id] = round(curr_a_elo + 32.0 * (s_a - e_a))

            # 更新 Recent Results (最多保留最近10场)
            results_tracker[h_id] = (_get_recent(h_id) + char_h)[-10:]
            results_tracker[a_id] = (_get_recent(a_id) + char_a)[-10:]

        training = []

        for match in matches:
            h_id = match.home_team_id
            a_id = match.away_team_id

            # 提取赛前历史快照，防止未来泄露
            h_elo_pre = _get_elo(h_id)
            a_elo_pre = _get_elo(a_id)
            h_recent_pre = _get_recent(h_id)
            a_recent_pre = _get_recent(a_id)

            ctx = self._reconstruct_context(
                match,
                home_elo=h_elo_pre,
                away_elo=a_elo_pre,
                home_recent=h_recent_pre,
                away_recent=a_recent_pre,
            )
            if ctx is None:
                continue

            if elo_diff_range and elo_diff_range != "all":
                tier = _elo_diff_tier(ctx.home_team.elo, ctx.away_team.elo)
                if tier != elo_diff_range:
                    # 即使当前样本不加入该 range 的训练集，也必须更新状态追踪器以保持时间线演进
                    _update_trackers(h_id, a_id, match.actual_outcome)
                    continue

            training.append((ctx, match.actual_outcome))
            _update_trackers(h_id, a_id, match.actual_outcome)

        logger.info(
            f"[weight-learner] Fetched {len(training)} training samples"
            f" (stage={stage or 'all'}, elo={elo_diff_range or 'all'})"
        )
        return training

    def _compute_form_factor(self, form: str) -> float:
        """从历史战绩序列动态推算基础状态因子，完全防泄漏"""
        if not form:
            return 1.0
        score = sum(0.1 if c == "W" else (-0.1 if c == "L" else 0.0) for c in form)
        return max(0.5, min(1.5, 1.0 + score))

    def _reconstruct_context(
        self,
        match: Match,
        home_elo: int = 1500,
        away_elo: int = 1500,
        home_recent: str = "",
        away_recent: str = "",
    ) -> Optional[MatchContext]:
        """从 Match + Team 重建 MatchContext（使用无泄露的时序快照 Elo 与近况）"""
        home = match.home_team
        away = match.away_team
        if not home or not away:
            return None

        # ─── 历史状态与舆情无泄漏还原 ───
        h_factor_val = None
        a_factor_val = None
        h_injuries_val = ""
        a_injuries_val = ""

        # 优先读取当时赛前关联报告中的隐藏 JSON 快照 (JSON_SNAPSHOT)
        ai_report = self.db.query(MatchAIReport).filter(MatchAIReport.match_id == match.id).first()
        if ai_report and ai_report.content:
            import re
            match_snapshot = re.search(r"<!--\s*JSON_SNAPSHOT:\s*(\{.*?\})\s*-->", ai_report.content)
            if match_snapshot:
                try:
                    snapshot = json.loads(match_snapshot.group(1))
                    h_factor_val = snapshot.get("home_factor")
                    a_factor_val = snapshot.get("away_factor")
                    h_injuries_val = snapshot.get("home_injuries", "")
                    a_injuries_val = snapshot.get("away_injuries", "")
                except Exception:
                    pass

        # 兜底 Fallback：如无赛前舆情报告，直接根据历史战绩序列计算当时的状态因子（防未来泄漏）
        if h_factor_val is None:
            h_factor_val = self._compute_form_factor(home_recent)
        if a_factor_val is None:
            a_factor_val = self._compute_form_factor(away_recent)

        return MatchContext(
            match_id=match.id,
            home_team=TeamContext(
                team_id=home.id,
                name=home.name,
                elo=home_elo,
                fifa_rank=home.fifa_rank or 100,
                avg_goals_scored=home.avg_goals_scored or 1.3,
                avg_goals_conceded=home.avg_goals_conceded or 1.3,
                form_factor=h_factor_val,  # 💡 替换为防泄漏历史值
                key_players_available=11,
                key_players_total=11,
                squad_fatigue_index=home.squad_fatigue_index or 0.5,
                tactical_style=home.tactical_style or "balanced",
                coach_rating=home.coach_rating or 0.5,
                home_away_factor=home.home_away_factor or 1.0,
                weather_adaptability=home.weather_adaptability or 1.0,
                recent_results=home_recent,
            ),
            away_team=TeamContext(
                team_id=away.id,
                name=away.name,
                elo=away_elo,
                fifa_rank=away.fifa_rank or 100,
                avg_goals_scored=away.avg_goals_scored or 1.3,
                avg_goals_conceded=away.avg_goals_conceded or 1.3,
                form_factor=a_factor_val,  # 💡 替换为防泄漏历史值
                key_players_available=11,
                key_players_total=11,
                squad_fatigue_index=away.squad_fatigue_index or 0.5,
                tactical_style=away.tactical_style or "balanced",
                coach_rating=away.coach_rating or 0.5,
                home_away_factor=away.home_away_factor or 1.0,
                weather_adaptability=away.weather_adaptability or 1.0,
                recent_results=away_recent,
            ),
            stage=match.stage or "group",
            is_knockout=match.stage not in (None, "", "group"),
            odds_home=match.odds_home,
            odds_draw=match.odds_draw,
            odds_away=match.odds_away,
            closing_odds_home=match.closing_odds_home,
            closing_odds_draw=match.closing_odds_draw,
            closing_odds_away=match.closing_odds_away,
            venue_type=match.venue_type or "neutral",
            weather=match.weather or "clear",
            temperature=match.temperature or 20.0,
            pitch_condition=match.pitch_condition or "good",
            schedule_density=match.schedule_density or "normal",
        )

    def precompute(
        self,
        data: List[Tuple[MatchContext, str]],
    ) -> List[PrecomputedSample]:
        """预计算所有样本的子模型输出，避免优化时重复推理"""
        samples = []
        total = len(data)
        log_interval = max(1, total // 10)

        for idx, (ctx, actual) in enumerate(data):
            try:
                elo_out = EloModel.predict(ctx)
                poisson_spf = PoissonModel.predict_spf_only(ctx)
                players_factor = PlayerAdjustmentModel.predict(ctx)
                market_out = MarketModel.predict(ctx)

                samples.append(PrecomputedSample(
                    elo_home=elo_out["home"],
                    elo_draw=elo_out["draw"],
                    elo_away=elo_out["away"],
                    poisson_home=poisson_spf["home"],
                    poisson_draw=poisson_spf["draw"],
                    poisson_away=poisson_spf["away"],
                    players_factor=players_factor,
                    market_home=market_out.get("home", 1 / 3.0) if market_out else 1 / 3.0,
                    market_draw=market_out.get("draw", 1 / 3.0) if market_out else 1 / 3.0,
                    market_away=market_out.get("away", 1 / 3.0) if market_out else 1 / 3.0,
                    has_market=market_out is not None,
                    actual=actual,
                ))
            except Exception:
                continue

            if (idx + 1) % log_interval == 0:
                logger.info(f"[weight-learner] Precompute progress: {idx + 1}/{total}")

        logger.info(f"[weight-learner] Precomputed {len(samples)}/{total} samples")
        return samples

    def _to_arrays(self, samples: List[PrecomputedSample]) -> Tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray
    ]:
        """将预计算样本转为 numpy 数组用于向量化计算"""
        n = len(samples)
        elo = np.zeros((n, 3))
        poisson = np.zeros((n, 3))
        players = np.zeros(n)
        market = np.zeros((n, 3))
        has_market = np.zeros(n, dtype=bool)
        actual_idx = np.zeros(n, dtype=int)

        for i, s in enumerate(samples):
            elo[i] = [s.elo_home, s.elo_draw, s.elo_away]
            poisson[i] = [s.poisson_home, s.poisson_draw, s.poisson_away]
            players[i] = s.players_factor
            market[i] = [s.market_home, s.market_draw, s.market_away]
            has_market[i] = s.has_market
            actual_idx[i] = {"home": 0, "draw": 1, "away": 2}.get(s.actual, 0)

        return elo, poisson, players, market, has_market, actual_idx

    def learn(
        self,
        data: List[Tuple[MatchContext, str]],
        metric: str = "brier",
        stage: str = "all",
        elo_diff_range: str = "all",
    ) -> Optional[LearnedWeights]:
        """
        对给定训练数据，用 scipy.optimize.minimize 学习最优权重。
        预计算子模型输出后用向量化目标函数加速。
        """
        if len(data) < 10:
            logger.warning(
                f"[weight-learner] Insufficient data: {len(data)} samples, skipping"
            )
            return None

        # 预计算子模型输出
        samples = self.precompute(data)
        if len(samples) < 10:
            return None

        elo, poisson, players, market, has_market, actual_idx = self._to_arrays(samples)

        x0 = _dict_to_weights(DEFAULT_WEIGHTS)
        bounds = _get_weight_bounds()

        result: OptimizeResult = minimize(
            fun=_objective_fast,
            x0=x0,
            args=(elo, poisson, players, market, has_market, actual_idx, metric),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 300, "disp": False},
        )

        if not result.success:
            logger.warning(f"[weight-learner] Optimization failed: {result.message}")

        learned = _weights_to_dict(result.x)
        metric_value = _objective_fast(
            result.x, elo, poisson, players, market, has_market, actual_idx, metric
        )

        logger.info(
            f"[weight-learner] Learned weights for {stage}/{elo_diff_range}: "
            f"Elo={learned['elo']:.3f}, Poisson={learned['poisson']:.3f}, "
            f"Players={learned['players']:.3f}, Market={learned['market']:.3f} "
            f"| {metric}={metric_value:.4f} (n={len(samples)})"
        )

        return LearnedWeights(
            stage=stage,
            elo_diff_range=elo_diff_range,
            weights=learned,
            metric=metric,
            metric_value=metric_value,
            sample_size=len(samples),
        )

    def save(self, lw: LearnedWeights) -> None:
        """保存学习到的权重到 FusionWeight 表，并标记旧权重为 inactive"""
        self.db.query(FusionWeight).filter(
            FusionWeight.stage == lw.stage,
            FusionWeight.elo_diff_range == lw.elo_diff_range,
        ).update({"is_active": False}, synchronize_session=False)

        fw = FusionWeight(
            stage=lw.stage,
            elo_diff_range=lw.elo_diff_range,
            weights=lw.weights,
            metric=lw.metric,
            metric_value=lw.metric_value,
            sample_size=lw.sample_size,
            is_active=True,
        )
        self.db.add(fw)
        self.db.commit()
        logger.info(
            f"[weight-learner] Saved weights to DB: {lw.stage}/{lw.elo_diff_range}"
        )

    def learn_all(
        self,
        metric: str = "brier",
        stages: Optional[List[str]] = None,
        elo_tiers: Optional[List[str]] = None,
    ) -> Dict[str, LearnedWeights]:
        """
        批量学习多维度权重。
        全局(all/all) + ELO分档
        """
        if stages is None:
            stages = ["all"]
        if elo_tiers is None:
            elo_tiers = ["all", "0-100", "100-200", "200-400", "400+"]

        results = {}

        # 1. 全局权重（最重要，所有数据一起学）
        data_all = self.fetch_training_data(stage="all", elo_diff_range="all")
        lw_all = self.learn(data_all, metric=metric, stage="all", elo_diff_range="all")
        if lw_all:
            self.save(lw_all)
            results["all/all"] = lw_all

        # 2. 按 elo_tier 学习（跨全部 stage）
        for tier in elo_tiers:
            if tier == "all":
                continue
            data = self.fetch_training_data(stage="all", elo_diff_range=tier)
            lw = self.learn(data, metric=metric, stage="all", elo_diff_range=tier)
            if lw:
                self.save(lw)
                results[f"all/{tier}"] = lw

        logger.info(f"[weight-learner] Total learned configs: {len(results)}")
        return results


# ────────────────────────────
# CLI
# ────────────────────────────
if __name__ == "__main__":
    from database.models import SessionLocal

    db = SessionLocal()
    try:
        learner = WeightLearner(db)
        results = learner.learn_all(metric="brier")
        print(f"\n✅ 学习完成，共 {len(results)} 组权重")
        for key, lw in results.items():
            print(
                f"  {key}: Elo={lw.weights['elo']:.2f}, "
                f"Poisson={lw.weights['poisson']:.2f}, "
                f"Players={lw.weights['players']:.2f}, "
                f"Market={lw.weights['market']:.2f} "
                f"| Brier={lw.metric_value:.4f} (n={lw.sample_size})"
            )
    finally:
        db.close()
