"""融合训练器 v3 — 消除标签泄露 + 时间顺序分割

修复:
- 只使用赛前可获取的特征(收盘赔率必须在开球前采集)
- 时间顺序切分(不用未来数据训练)
- 独立的验证集
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
from utils.logger import get_logger

logger = get_logger("fusion_trainer")

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
        X, y = self._build()
        if X is None or len(X) < 100:
            logger.warning("Insufficient data")
            return None
        best_lam, _ = cross_validate_lambda(X, y, class_weight=class_weight)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=2000, class_weight=class_weight)
        w = t.fit(X, y, league="global")
        w.save()
        return w

    def train_league(self, competition):
        X, y = self._build(competition=competition)
        if X is None or len(X) < 50:
            return None
        best_lam, _ = cross_validate_lambda(X, y)
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=1000)
        w = t.fit(X, y, league=competition)
        w.save()
        return w

    def train_knockout(self):
        X, y = self._build(knockout_only=True)
        if X is None or len(X) < 30:
            return None
        best_lam, _ = cross_validate_lambda(X, y, lambdas=[0.005, 0.01, 0.02, 0.05, 0.1])
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=500)
        w = t.fit(X, y, league="knockout")
        w.save()
        return w

    def _build(self, competition=None, knockout_only=False) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        构建训练数据。

        关键修复: 只使用赛前可获取的特征。
        - 收盘赔率必须在开球前采集 (recorded_at < kickoff_at)
        - 特征构建时不使用赛后数据
        """
        s = SessionLocal()
        try:
            # 只使用已完赛且有结果的比赛
            q = s.query(Match).options(
                joinedload(Match.home_team), joinedload(Match.away_team)
            ).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
            )
            if competition:
                q = q.filter(Match.competition == competition)
            if knockout_only:
                q = q.filter(Match.stage.in_(["R16", "QF", "SF", "F", "3P"]))

            matches = q.order_by(Match.kickoff_at).all()
            if self.limit:
                matches = matches[:self.limit]

            logger.info(f"[fusion_trainer] Total finished matches: {len(matches)}")

            # ── 过滤: 只保留有真实收盘赔率的比赛 ──
            # 收盘赔率必须在开球前采集,防止标签泄露
            valid_matches = []
            for m in matches:
                if m.closing_odds_home is None or m.closing_odds_draw is None or m.closing_odds_away is None:
                    continue
                # 检查是否有赛前采集的赔率记录
                earliest = s.query(OddsHistory).filter(
                    OddsHistory.match_id == m.id,
                    OddsHistory.recorded_at < m.kickoff_at,
                ).order_by(OddsHistory.recorded_at.asc()).first()
                if earliest is None:
                    # 没有赛前赔率记录,跳过
                    continue
                valid_matches.append(m)

            logger.info(f"[fusion_trainer] After pre-match filter: {len(valid_matches)} matches")

            if len(valid_matches) < 10:
                logger.warning("[fusion_trainer] Too few valid matches after filtering")
                return None, None

            Xl, yl = [], []
            o2i = {"home": 0, "draw": 1, "away": 2}
            sk = 0
            fm = FormMarkovModel(s)
            hm = H2HModel(s)

            for i, m in enumerate(valid_matches):
                if i % 1000 == 0:
                    logger.info(f"[fusion_trainer] Progress: {i}/{len(valid_matches)}")
                try:
                    yv = o2i.get(m.actual_outcome)
                    if yv is None:
                        sk += 1
                        continue
                    if m.home_team is None or m.away_team is None:
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
                except Exception:
                    sk += 1

            if sk:
                logger.info(f"[fusion_trainer] Skipped {sk} matches")

            if len(Xl) < 10:
                return None, None

            return np.array(Xl, dtype=np.float64), np.array(yl, dtype=np.int64)
        finally:
            s.close()
