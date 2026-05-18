"""融合训练器 v2 — ORM joinedload 修正版"""
from typing import Dict, Optional, Tuple
import numpy as np
from sqlalchemy.orm import joinedload
from models import SessionLocal, Match, MatchStatus
from prediction_engine import build_context_from_match
from features import EloModel, PoissonModel, MarketModel
from features.adjustment_models import PlayerAdjustmentModel
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel
from features.feature_builder import FeatureBuilder
from fusion.logistic_fusion import LogisticFusionTrainer, LogisticFusionWeights, cross_validate_lambda
from logger import get_logger
logger = get_logger("fusion_trainer")
LEAGUE_GROUPS = {"EPL":"epl","LaLiga":"laliga","Bundesliga":"bundesliga","SerieA":"seriea","Ligue1":"ligue1","JLeague":"jleague","KLeague":"kleague","UCL":"ucl"}

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
        best_lam, _ = cross_validate_lambda(X, y, lambdas=[0.005,0.01,0.02,0.05,0.1])
        t = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=500)
        w = t.fit(X, y, league="knockout")
        w.save()
        return w

    def _build(self, competition=None, knockout_only=False) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        s = SessionLocal()
        try:
            q = s.query(Match).options(
                joinedload(Match.home_team), joinedload(Match.away_team)
            ).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
            )
            if competition:
                q = q.filter(Match.competition == competition)
            if knockout_only:
                q = q.filter(Match.stage.in_(["R16","QF","SF","F","3P"]))
            matches = q.order_by(Match.kickoff_at).all()
            if self.limit:
                matches = matches[:self.limit]
            logger.info(f"[fusion_trainer] {len(matches)} matches")
            Xl, yl = [], []
            o2i = {"home":0,"draw":1,"away":2}
            sk = 0
            fm = FormMarkovModel(s)
            hm = H2HModel(s)
            for m in matches:
                try:
                    yv = o2i.get(m.actual_outcome)
                    if yv is None: sk+=1; continue
                    if m.home_team is None or m.away_team is None: sk+=1; continue
                    ctx = build_context_from_match(m)
                    e = EloModel.predict(ctx)
                    p = PoissonModel.predict(ctx)
                    pl = PlayerAdjustmentModel.predict(ctx)
                    mk = MarketModel.predict(ctx)
                    ff = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id, is_home=True)
                    hf = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
                    xv = self.feature_builder.build(e, p, pl, mk, ff, hf, ctx)
                    Xl.append(xv); yl.append(yv)
                except:
                    sk += 1
            if sk:
                logger.info(f"[fusion_trainer] Skipped {sk}")
            if len(Xl) < 10:
                return None, None
            return np.array(Xl, dtype=np.float64), np.array(yl, dtype=np.int64)
        finally:
            s.close()
