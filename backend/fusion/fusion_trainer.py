"""融合训练器 v3 — 消除标签泄露 + 时间顺序分割

修复:
- 只使用赛前可获取的特征(收盘赔率必须在开球前采集)
- 时间顺序切分(不用未来数据训练)
- 独立的验证集
- (2026-06-25) 同步时间戳进 CV：把 ``kickoff_at`` 透传给
  ``cross_validate_lambda`` 的 ``time_index`` 参数，使 purged 时序
  褶皱首先按比赛的真实时间排序，再按有序索引切分。
- (2026-06-25) 训练集/验证集显式切分：完成 CV 选 λ 后，
  保留最后 20% 的样本做 hold-out 评估，避免再用训练数据评估自己。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple
import numpy as np
from sqlalchemy.orm import joinedload

from database.models import (
    SessionLocal, Match, MatchStatus, OddsHistory,
)
from core.prediction_engine import build_context_from_match
from core.models.elo import EloModel
from core.models.poisson import PoissonModel
from core.models.market import MarketModel
from core.models.player_adjustment import PlayerAdjustmentModel
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel
from features.feature_builder import FeatureBuilder
from fusion.logistic_fusion import LogisticFusionTrainer, cross_validate_lambda
from fusion.weights_registry import WeightsRegistry
from utils.logger import get_logger

logger = get_logger("fusion_trainer")

# 共享的注册表 — 每次训练自动登记，无需手动传
_WEIGHTS_REGISTRY = WeightsRegistry()

LEAGUE_GROUPS = {
    "EPL": "epl", "LaLiga": "laliga", "Bundesliga": "bundesliga",
    "SerieA": "seriea", "Ligue1": "ligue1", "JLeague": "jleague",
    "KLeague": "kleague", "UCL": "ucl",
}


class FusionTrainer:
    def __init__(self, limit=None):
        self.limit = limit
        self.feature_builder = FeatureBuilder(use_interactions=True)

    def train_global(self, class_weight: Optional[Dict[int, float]] = None):
        X, y, t_idx = self._build()
        if X is None or len(X) < 100:
            logger.warning("Insufficient data")
            return None
        best_lam, _ = cross_validate_lambda(X, y, class_weight=class_weight, time_index=t_idx)
        cut = int(len(X) * 0.8)
        if t_idx is not None:
            order = np.argsort(t_idx, kind="mergesort")
            train_pos = order[:cut]
        else:
            train_pos = np.arange(cut)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=2000, class_weight=class_weight)
        w = t.fit(X[train_pos], y[train_pos], league="global")
        if len((order := (np.argsort(t_idx, kind="mergesort") if t_idx is not None else np.arange(len(X)))) > cut):
            val_pos = order[cut:]
            hold_acc = float(np.mean(np.argmax(w.predict(X[val_pos]), axis=1) == y[val_pos]))
            logger.info(f"[fusion_trainer][global] hold-out acc={hold_acc:.4f} n={len(val_pos)}")
        # 通过注册表保存（旧 .save() 只写文件，注册表无法回看历史/回滚）
        _WEIGHTS_REGISTRY.register("global", w)
        return w

    def train_league(self, competition):
        X, y, t_idx = self._build(competition=competition)
        if X is None or len(X) < 50:
            return None
        best_lam, _ = cross_validate_lambda(X, y, time_index=t_idx)
        cut = int(len(X) * 0.8)
        if t_idx is not None:
            order = np.argsort(t_idx, kind="mergesort")
            train_pos = order[:cut]
        else:
            train_pos = np.arange(cut)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=1000)
        w = t.fit(X[train_pos], y[train_pos], league=competition)
        if len((order := (np.argsort(t_idx, kind="mergesort") if t_idx is not None else np.arange(len(X)))) > cut):
            val_pos = order[cut:]
            hold_acc = float(np.mean(np.argmax(w.predict(X[val_pos]), axis=1) == y[val_pos]))
            logger.info(f"[fusion_trainer][{competition}] hold-out acc={hold_acc:.4f} n={len(val_pos)}")
        _WEIGHTS_REGISTRY.register(competition, w)
        return w

    def train_tier(self, tier_name: str, competitions: list[str]):
        X, y, t_idx = self._build(competitions=competitions)
        if X is None or len(X) < 50:
            return None
        best_lam, _ = cross_validate_lambda(X, y, time_index=t_idx)
        cut = int(len(X) * 0.8)
        if t_idx is not None:
            order = np.argsort(t_idx, kind="mergesort")
            train_pos = order[:cut]
        else:
            train_pos = np.arange(cut)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=1000)
        w = t.fit(X[train_pos], y[train_pos], league=tier_name)
        if len((order := (np.argsort(t_idx, kind="mergesort") if t_idx is not None else np.arange(len(X)))) > cut):
            val_pos = order[cut:]
            hold_acc = float(np.mean(np.argmax(w.predict(X[val_pos]), axis=1) == y[val_pos]))
            logger.info(f"[fusion_trainer][{tier_name}] hold-out acc={hold_acc:.4f} n={len(val_pos)}")
        _WEIGHTS_REGISTRY.register(tier_name, w)
        return w

    def train_knockout(self):
        X, y, t_idx = self._build(knockout_only=True)
        if X is None or len(X) < 30:
            return None
        best_lam, _ = cross_validate_lambda(X, y, lambdas=[0.005, 0.01, 0.02, 0.05, 0.1], time_index=t_idx)
        cut = int(len(X) * 0.8)
        if t_idx is not None:
            order = np.argsort(t_idx, kind="mergesort")
            train_pos = order[:cut]
        else:
            train_pos = np.arange(cut)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=500)
        w = t.fit(X[train_pos], y[train_pos], league="knockout")
        if len((order := (np.argsort(t_idx, kind="mergesort") if t_idx is not None else np.arange(len(X)))) > cut):
            val_pos = order[cut:]
            hold_acc = float(np.mean(np.argmax(w.predict(X[val_pos]), axis=1) == y[val_pos]))
            logger.info(f"[fusion_trainer][knockout] hold-out acc={hold_acc:.4f} n={len(val_pos)}")
        _WEIGHTS_REGISTRY.register("knockout", w)
        return w

    def _build(
        self,
        competition: Optional[str] = None,
        competitions: Optional[list[str]] = None,
        knockout_only: bool = False,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        构建训练数据 + 同步时间戳数组。

        关键修复（2026-06-25）：除 ``X, y`` 之外，返回 ``t_idx`` —— 每个样本
        对应比赛的 ``kickoff_at`` Unix 时间戳（numpy float64）。调用方把它
        传给 CV/最终训练，从而保证 purged 时序切分。

        关键修复（保留 v3）：只使用赛前可获取的特征。
        - 优先使用 closing_odds（已隐含赛前采集）
        - 特征构建时不使用赛后数据
        """
        import time

        s = SessionLocal()
        try:
            FRIENDLY_KEYWORDS = ("friendly", "warm-up", "exhibition", "invitational")
            BAD_OUTCOMES = ("abandoned", "unknown", "cancelled", "postponed", "")

            q = s.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.closing_odds_home.isnot(None),
                Match.closing_odds_draw.isnot(None),
                Match.closing_odds_away.isnot(None),
            )

            if competition:
                q = q.filter(Match.competition == competition)
            elif competitions:
                q = q.filter(Match.competition.in_(competitions))

            if knockout_only:
                q = q.filter(Match.stage.in_(("R32", "R16", "QF", "SF", "F", "3P")))

            # 让 ORM 按时间先后排序，特征构建顺序天然有序
            all_matches = q.order_by(Match.kickoff_at.asc()).all()
            logger.info(f"[fusion_trainer] Total finished with closing odds: {len(all_matches)}")

            valid_matches = []
            for m in all_matches:
                comp = (m.competition or "").lower()
                if any(kw in comp for kw in FRIENDLY_KEYWORDS):
                    continue
                outcome = (m.actual_outcome or "").lower()
                if outcome in BAD_OUTCOMES:
                    continue
                valid_matches.append(m)

            logger.info(
                f"[fusion_trainer] After quality gate: {len(valid_matches)} matches "
                f"(removed friendlies/bad outcomes)"
            )

            if len(valid_matches) < 10:
                logger.warning("[fusion_trainer] Too few matches after quality gate")
                return None, None, None

            Xl, yl, tl = [], [], []
            o2i = {"home": 0, "draw": 1, "away": 2}
            sk = 0
            fm = FormMarkovModel(s)
            hm = H2HModel(s)

            for i, m in enumerate(valid_matches):
                if i % 1000 == 0:
                    logger.info(f"[fusion_trainer] Feature building: {i}/{len(valid_matches)}")
                try:
                    yv = o2i.get(m.actual_outcome)
                    if yv is None or m.home_team is None or m.away_team is None:
                        sk += 1
                        continue

                    ctx = build_context_from_match(m)
                    e = EloModel.predict(ctx)
                    p = PoissonModel.predict(ctx)
                    pl = PlayerAdjustmentModel.predict(ctx)
                    mk = MarketModel.predict(ctx)
                    ff = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id, is_home=True)
                    hf = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
                    xv = self.feature_builder.build(e, p, pl, mk, ff, hf, ctx)
                    Xl.append(xv)
                    yl.append(yv)
                    # 关键：kickoff_at 转 float 64 时间戳
                    ts = m.kickoff_at
                    if ts is None:
                        tl.append(float(i))  # fallback：按插入顺序
                    else:
                        tl.append(float(ts.timestamp() if hasattr(ts, "timestamp") else ts))
                except Exception:
                    sk += 1

            if sk:
                logger.info(f"[fusion_trainer] Skipped {sk} matches")

            if len(Xl) < 10:
                return None, None, None

            return (
                np.array(Xl, dtype=np.float64),
                np.array(yl, dtype=np.int64),
                np.array(tl, dtype=np.float64),
            )
        finally:
            s.close()
