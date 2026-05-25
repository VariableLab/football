"""Debug: 检查单场比赛特征构建"""
import traceback
from sqlalchemy.orm import joinedload
from database.models import SessionLocal, Match, MatchStatus, Team
from core.prediction_engine import build_team_context_from_orm, build_context_from_match

session = SessionLocal()
m = session.query(Match).options(
    joinedload(Match.home_team), joinedload(Match.away_team)
).filter(
    Match.status == 'finished',
    Match.actual_outcome.isnot(None),
    Match.odds_home.isnot(None),
).first()

print(f"Match: {m.match_code}  {m.home_team.name} vs {m.away_team.name}  outcome={m.actual_outcome}")

h = build_team_context_from_orm(m.home_team)
print(f"  home ctx: OK  elo={h.elo}  name={h.name}  rest={h.rest_days}  form={h.recent_results[:20] if h.recent_results else 'None'}")

a = build_team_context_from_orm(m.away_team)
print(f"  away ctx: OK  elo={a.elo}  name={a.name}")

ctx = build_context_from_match(m)
print(f"  ctx: OK  handicap={ctx.handicap}  is_knockout={ctx.is_knockout}  has_odds={ctx.has_odds}  has_closing={ctx.has_closing_odds}")

# Test feature chain
from features import EloModel, PoissonModel, MarketModel
from features.adjustment_models import PlayerAdjustmentModel
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel
from features.feature_builder import FeatureBuilder

elo = EloModel.predict(ctx)
pois = PoissonModel.predict(ctx)
players = PlayerAdjustmentModel.predict(ctx)
market = MarketModel.predict(ctx)
form_m = FormMarkovModel(session)
form_f = form_m.compute(h.recent_results, h.team_id, is_home=True)
h2h_m = H2HModel(session)
h2h_f = h2h_m.compute(h.team_id, a.team_id)
builder = FeatureBuilder(use_interactions=True)
x = builder.build(elo, pois, players, market, form_f, h2h_f, ctx)
print(f"\nFeature vector: shape={x.shape}")
print(f"  Elo:      {elo}")
print(f"  Poisson:  {pois['spf']}")
print(f"  Players:  {players:.4f}")
print(f"  Market:   {market}")
print(f"  Form:     state={form_f.state}  momentum={form_f.momentum}")
print(f"  H2H:      total={h2h_f.total}  win%={h2h_f.home_win_pct}")
print(f"\nAll OK!")

session.close()
