"""Phase 1 集成回测: FeatureBuilder + LogisticFusion (修正版)"""
import sys, time, numpy as np
from sqlalchemy.orm import joinedload

print("=" * 60)
print("Step 1: 导入验证")
t0 = time.time()
from models import SessionLocal, Match, MatchStatus
from prediction_engine import build_context_from_match
from features import EloModel, PoissonModel, MarketModel
from features.adjustment_models import PlayerAdjustmentModel
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel
from features.feature_builder import FeatureBuilder
from fusion.logistic_fusion import LogisticFusionTrainer, cross_validate_lambda
print(f"  导入通过 ({time.time()-t0:.1f}s)")

print("\nStep 2: 加载历史比赛 (with joinedload)")
session = SessionLocal()
matches = session.query(Match).options(
    joinedload(Match.home_team), joinedload(Match.away_team)
).filter(
    Match.status == MatchStatus.FINISHED,
    Match.actual_outcome.isnot(None),
).order_by(Match.kickoff_at.desc()).limit(1000).all()
print(f"  加载 {len(matches)} 场")

print("\nStep 3: 逐场构建特征")
builder = FeatureBuilder(use_interactions=True)
X_list, y_list = [], []
outcome_map = {"home": 0, "draw": 1, "away": 2}
skipped = 0

form_model = FormMarkovModel(session)
h2h_model = H2HModel(session)

for i, m in enumerate(matches[:500]):
    if i % 100 == 0:
        print(f"  {i}/500... ({len(X_list)} ok, {skipped} skip)")
    try:
        y_val = outcome_map.get(m.actual_outcome)
        if y_val is None: skipped += 1; continue
        if m.home_team is None or m.away_team is None: skipped += 1; continue
        
        ctx = build_context_from_match(m)
        elo_probs = EloModel.predict(ctx)
        poisson_result = PoissonModel.predict(ctx)
        players_factor = PlayerAdjustmentModel.predict(ctx)
        market_probs = MarketModel.predict(ctx)
        
        form_f = form_model.compute(
            ctx.home_team.recent_results, ctx.home_team.team_id, is_home=True
        )
        h2h_f = h2h_model.compute(ctx.home_team.team_id, ctx.away_team.team_id)
        
        x_vec = builder.build(elo_probs, poisson_result, players_factor, market_probs, form_f, h2h_f, ctx)
        X_list.append(x_vec)
        y_list.append(y_val)
    except Exception as e:
        skipped += 1

X = np.array(X_list, dtype=np.float64)
y = np.array(y_list, dtype=np.int64)
print(f"  完成: {len(X_list)} 样本, 跳过 {skipped}")
print(f"  维度: {X.shape}  标签: home={np.sum(y==0)} draw={np.sum(y==1)} away={np.sum(y==2)}")

print("\nStep 4: 训练 LR (80/20)")
split = int(len(X) * 0.8)
X_train, y_train = X[:split], y[:split]
X_val, y_val = X[split:], y[split:]
print(f"  训练: {len(X_train)}, 验证: {len(X_val)}")

t0 = time.time()
best_lam, cv_r = cross_validate_lambda(X_train, y_train, lambdas=[0.0001, 0.001, 0.01], n_folds=3)
print(f"  CV best lambda={best_lam} ({time.time()-t0:.1f}s)")

trainer = LogisticFusionTrainer(l1_penalty=best_lam, max_iter=1000)
weights = trainer.fit(X_train, y_train, league="backtest")
print(f"  训练集 acc: {weights.accuracy:.4f}  CE: {weights.cross_entropy:.4f}")

print("\nStep 5: 验证集评估")
probs_val = weights.predict(X_val)
if isinstance(probs_val, np.ndarray):
    preds = np.argmax(probs_val, axis=1)
    acc = np.mean(preds == y_val)
    y_oh = np.zeros((len(y_val), 3))
    y_oh[np.arange(len(y_val)), y_val] = 1
    brier = np.mean(np.sum((probs_val - y_oh)**2, axis=1))
    print(f"  验证集准确率: {acc:.4f} ({acc*100:.1f}%)")
    print(f"  验证集 Brier:  {brier:.4f}")
    print(f"  随机基线:      33.3% / Brier 0.222")

print("\nStep 6: Top 10 特征重要性")
importance = np.abs(weights.coef_home) + np.abs(weights.coef_away)
top_idx = np.argsort(-importance)[:10]
feat_names = [
    "elo_diff","elo_win","elo_draw","elo_away","heavy_fav","heavy_udog","elo_tier",
    "lam_h","lam_a","lam_diff","pois_win","pois_draw","pois_away","goal_exp",
    "h_avail","a_avail","avail_d","inj_imp",
    "mkt_win","mkt_draw","mkt_away","overround","odds_move","src_cnt",
    "form_win","form_draw","momentum","stability","streak",
    "h2h_n","h2h_win","h2h_draw","h2h_rec","h2h_goal","1st_meet",
    "rest_adv","is_ko","is_derby",
    "I_elo_ko","I_disagree","I_mom_rest","I_mkt_src","I_elo_form",
]
for rank, i in enumerate(top_idx):
    name = feat_names[i] if i < len(feat_names) else f"f{i}"
    print(f"  {rank+1:2d}. {name:15s}  |coef|={importance[i]:.4f}  h={weights.coef_home[i]:+.4f}  a={weights.coef_away[i]:+.4f}")

session.close()
print(f"\n{'='*60}")
print("回测完成")
