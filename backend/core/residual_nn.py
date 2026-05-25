"""
Residual Bet Neural Network — 残差修正网络 (v2)

改造自原 BetNN:
  - 旧: one-hot 分类 → BCE loss
  - 新: 残差回归 → MSE loss → 修正 LR 系统性偏差
"""
import json, os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from logger import get_logger

logger = get_logger("residual_nn")

MODEL_DIR = "./data/bet_nn"
os.makedirs(MODEL_DIR, exist_ok=True)
RESIDUAL_MODEL_PATH = os.path.join(MODEL_DIR, "residual_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "residual_feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "residual_training_log.json")

INPUT_DIM = 33
OUTPUT_DIM = 3
BATCH_SIZE = 64
LEARNING_RATE = 1e-3
EPOCHS = 50
PATIENCE = 8
MIN_TRAIN_SAMPLES = 50

LEAGUE_MAP = {"EPL":[1,0,0,0],"Bundesliga":[0,1,0,0],"LaLiga":[0,0,1,0],"SerieA":[0,0,0,1]}


def extract_residual_features(lr_probs, spf_probs, rq_probs, score_top3, odds, elo_diff, odds_movement, competition, form_features=None):
    """36维: LR(3)+SPF/RQ(6)+Score(3)+Odds(3)+Elo(1)+Move(3)+League(4)+Form(5)+LRconf(2)+pad(3)"""
    feats = [lr_probs.get("home",0.33), lr_probs.get("draw",0.33), lr_probs.get("away",0.33)]
    for k in ["home","draw","away"]: feats.append(spf_probs.get(k,0.33))
    for k in ["home","draw","away"]: feats.append(rq_probs.get(k,0.33))
    top = sorted(score_top3.items(),key=lambda x:-x[1])[:3]
    for _,p in top: feats.append(p)
    while len(feats)<12: feats.append(0.0)
    for sel in ["home","draw","away"]: feats.append(1.0/max(odds.get(sel,2.0),1.01))
    feats.append(np.clip(elo_diff/400.0,-1.0,1.0))
    for sel in ["home","draw","away"]: feats.append(np.clip(odds_movement.get(sel,0.0)/0.1,-1.0,1.0))
    feats.extend(LEAGUE_MAP.get(competition,[0,0,0,0]))
    if form_features and len(form_features)>=5: feats.extend(form_features[:5])
    else: feats.extend([0.33,0.33,0.0,0.5,0.0])
    lr_vals = list(lr_probs.values())
    lr_max = max(lr_vals)
    lr_entropy = -sum(p*np.log(p+1e-8) for p in lr_vals)
    feats.extend([lr_max, min(lr_entropy/2.0,1.0)])
    feats.extend([0.0,0.0,0.0])
    return np.array(feats[:INPUT_DIM], dtype=np.float32)


class ResidualDataset(Dataset):
    def __init__(self, features, residuals):
        self.features = torch.FloatTensor(features)
        self.residuals = torch.FloatTensor(residuals)
    def __len__(self): return len(self.features)
    def __getitem__(self, idx): return self.features[idx], self.residuals[idx]


class ResidualNet(nn.Module):
    def __init__(self, input_dim=INPUT_DIM, hidden_dims=(64,32,16)):
        super().__init__()
        layers = []; prev = input_dim
        for h in hidden_dims:
            layers.extend([nn.Linear(prev,h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(0.3)])
            prev = h
        layers.append(nn.Linear(prev, OUTPUT_DIM))
        self.net = nn.Sequential(*layers)
    def forward(self, x): return self.net(x)
    def predict_delta(self, features):
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            return self.forward(x).squeeze(0).numpy()


class ResidualTrainer:
    def __init__(self):
        self.model = ResidualNet()
        self.feature_mean = None
        self.feature_std = None

    def build_training_data(self, lr_weights=None):
        from models import SessionLocal, Match, MatchStatus, Prediction, PlayType
        s = SessionLocal()
        try:
            if lr_weights is None:
                try:
                    import glob
                    lr_files = sorted(glob.glob("./data/weights/lr/global_*.json"))
                    if lr_files:
                        from fusion.logistic_fusion import LogisticFusionWeights
                        lr_weights = LogisticFusionWeights.load(lr_files[-1])
                except: pass

            from sqlalchemy.orm import joinedload
            finished = s.query(Match).options(
                joinedload(Match.home_team), joinedload(Match.away_team)
            ).filter(
                Match.status == MatchStatus.FINISHED, Match.actual_outcome.in_(["home", "draw", "away"]),
                Match.closing_odds_home != None, Match.closing_odds_home > 1.01,
            ).all()
            if len(finished) < MIN_TRAIN_SAMPLES: return None

            match_ids = [m.id for m in finished]
            all_preds = s.query(Prediction).filter(Prediction.match_id.in_(match_ids)).all()
            pred_map = {}
            for p in all_preds:
                probs = p.probabilities if isinstance(p.probabilities,dict) else (json.loads(p.probabilities) if p.probabilities else {})
                pred_map[(p.match_id, p.play_type)] = probs

            Xl, Rl = [], []
            for m in finished:
                spf = pred_map.get((m.id, PlayType.SPF)) or pred_map.get((m.id, "SPF"))
                if not spf: continue
                score_p = pred_map.get((m.id, PlayType.SCORE)) or pred_map.get((m.id, "SCORE")) or {}
                odds = {"home":m.closing_odds_home or 2.0,"draw":m.closing_odds_draw or 3.0,"away":m.closing_odds_away or 2.0}
                odds_move = {}
                for sel in ["home","draw","away"]:
                    c = getattr(m,f"closing_odds_{sel}",None) or 0
                    o = getattr(m,f"opening_odds_{sel}",None) or 0
                    odds_move[sel] = (c-o)/o if c and o else 0.0

                lr_probs = spf
                elo_diff = 0.0
                if lr_weights is not None:
                    try:
                        from prediction_engine import build_context_from_match
                        from features import EloModel, PoissonModel, MarketModel
                        from features.adjustment_models import PlayerAdjustmentModel
                        from features.form_markov_model import FormMarkovModel
                        from features.h2h_model import H2HModel
                        from features.feature_builder import FeatureBuilder
                        # Use m directly since it's already loaded with joinedload
                        if m.home_team:
                            ctx = build_context_from_match(m)
                            builder = FeatureBuilder(use_interactions=True)
                            e = EloModel.predict(ctx); p = PoissonModel.predict(ctx)
                            pl = PlayerAdjustmentModel.predict(ctx); mk = MarketModel.predict(ctx)
                            fm = FormMarkovModel(s); ff = fm.compute(ctx.home_team.recent_results,ctx.home_team.team_id,is_home=True)
                            hm = H2HModel(s); hf = hm.compute(ctx.home_team.team_id,ctx.away_team.team_id)
                            xv = builder.build(e,p,pl,mk,ff,hf,ctx)
                            lr_probs = lr_weights.predict(xv)
                            elo_diff = ctx.home_team.elo - ctx.away_team.elo
                    except: pass

                o2i = {"home":0,"draw":1,"away":2}
                y_oh = np.zeros(OUTPUT_DIM,dtype=np.float32)
                y_oh[o2i.get(m.actual_outcome,1)] = 1.0
                lr_arr = np.array([lr_probs.get("home",0.33),lr_probs.get("draw",0.33),lr_probs.get("away",0.33)],dtype=np.float32)
                residual = y_oh - lr_arr

                feats = extract_residual_features(lr_probs, spf, spf, score_p or spf, odds, elo_diff, odds_move, m.competition or "")
                Xl.append(feats); Rl.append(residual)

            if len(Xl) < MIN_TRAIN_SAMPLES: return None
            X = np.stack(Xl); R = np.stack(Rl)
            self.feature_mean = X.mean(axis=0)
            # Fix: Ensure std is never 0 to avoid division by zero
            self.feature_std = np.maximum(X.std(axis=0), 1e-8)
            X = (X-self.feature_mean)/self.feature_std
            logger.info(f"[residual-nn] {len(X)} samples, dim={X.shape[1]}")
            return X, R
        finally:
            s.close()

    def train(self):
        data = self.build_training_data()
        if data is None: return None
        X, R = data
        ds = ResidualDataset(X, R)
        n = len(ds); nt = int(n*0.8)
        tr, va = torch.utils.data.random_split(ds, [nt, n-nt])
        tl = DataLoader(tr, batch_size=BATCH_SIZE, shuffle=True)
        vl = DataLoader(va, batch_size=BATCH_SIZE)
        self.model = ResidualNet()
        opt = torch.optim.AdamW(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
        best = float("inf"); pc = 0; metrics = {"train_loss":[],"val_loss":[]}
        for ep in range(EPOCHS):
            self.model.train(); tloss = 0.0
            for bx,br in tl:
                opt.zero_grad(); delta = self.model(bx)
                loss = nn.functional.mse_loss(delta, br)
                loss.backward(); opt.step(); tloss += loss.item()
            tloss /= len(tl)
            self.model.eval(); vloss = 0.0
            with torch.no_grad():
                for bx,br in vl: vloss += nn.functional.mse_loss(self.model(bx), br).item()
            vloss /= len(vl)
            metrics["train_loss"].append(round(tloss,4)); metrics["val_loss"].append(round(vloss,4))
            if vloss < best: best = vloss; pc = 0; torch.save(self.model.state_dict(), RESIDUAL_MODEL_PATH)
            else: pc += 1
            if pc >= PATIENCE: logger.info(f"[residual-nn] Early stop epoch {ep}"); break
            if ep%10==0: logger.info(f"[residual-nn] E{ep}: train={tloss:.4f} val={vloss:.4f}")
        self._save_feature_stats(); self._save_training_log(metrics)
        return {"epochs":len(metrics["train_loss"]),"best_val_loss":round(best,6),"samples":n}

    def _save_feature_stats(self):
        if self.feature_mean is not None:
            with open(FEATURE_STATS_PATH,"w") as f: json.dump({"mean":self.feature_mean.tolist(),"std":self.feature_std.tolist()},f)

    def _save_training_log(self, metrics):
        with open(TRAINING_LOG_PATH,"w") as f: json.dump({"trained_at":datetime.now(timezone.utc).isoformat(),"metrics":metrics},f,indent=2)


class ResidualPredictor:
    def __init__(self):
        self.model = ResidualNet(); self.feature_mean = None; self.feature_std = None; self._load()
    def _load(self):
        if os.path.exists(RESIDUAL_MODEL_PATH): self.model.load_state_dict(torch.load(RESIDUAL_MODEL_PATH)); self.model.eval()
        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH) as f:
                s = json.load(f); self.feature_mean = np.array(s["mean"],dtype=np.float32); self.feature_std = np.array(s["std"],dtype=np.float32)
                # Fix: Ensure std is never 0 when loading
                self.feature_std = np.maximum(self.feature_std, 1e-8)
    def is_ready(self): return self.feature_mean is not None
    def predict_delta(self, lr_probs, **kwargs):
        if not self.is_ready(): return np.zeros(OUTPUT_DIM,dtype=np.float32)
        f = extract_residual_features(lr_probs=lr_probs, **kwargs)
        f = (f-self.feature_mean)/self.feature_std
        return self.model.predict_delta(f)
    @staticmethod
    def apply_correction(lr_probs, delta, alpha=0.3):
        lr = np.array([lr_probs["home"],lr_probs["draw"],lr_probs["away"]])
        c = lr + alpha*delta; c = np.clip(c,0.001,None); c = c/c.sum()
        return {"home":float(c[0]),"draw":float(c[1]),"away":float(c[2])}


def residual_nn_train_job():
    trainer = ResidualTrainer(); result = trainer.train()
    if result: logger.info(f"[residual-nn-job] Trained: {result}")
    else: logger.info("[residual-nn-job] Skipped (insufficient data)")
