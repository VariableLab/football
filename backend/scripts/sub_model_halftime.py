"""
半场预测子模型 — MLP

特征: 主队半场倾向 + 客队半场倾向 + Elo差 + 主客场 + 联赛类型 + 赔率隐含概率
目标: 半场胜/平/负 (3分类)
训练标签: ht_home_goals, ht_away_goals → home/draw/away

独立训练，与主模型共享赛事类型权重概念。
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from utils.logger import get_logger

logger = get_logger("sub_model_halftime")

MODEL_DIR = "./data/sub_models/halftime"
os.makedirs(MODEL_DIR, exist_ok=True)

MODEL_PATH = os.path.join(MODEL_DIR, "halftime_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "training_log.json")

INPUT_DIM = 14
OUTPUT_DIM = 3  # home / draw / away

BATCH_SIZE = 64
LEARNING_RATE = 1e-3
EPOCHS = 60
PATIENCE = 7
MIN_TRAIN_SAMPLES = 100

LEAGUE_MAP = {
    "EPL": [1, 0, 0, 0],
    "Bundesliga": [0, 1, 0, 0],
    "LaLiga": [0, 0, 1, 0],
    "SerieA": [0, 0, 0, 1],
}


# ────────────────────────────
# 特征工程
# ────────────────────────────
def extract_halftime_features(
    home_ht_rate: Dict[str, float],
    away_ht_rate: Dict[str, float],
    elo_diff: float,
    venue_type: str,
    odds: Dict[str, float],
    competition: str,
) -> np.ndarray:
    """
    14维特征向量:
    - home_ht_rate(3): 主队近10场半场胜平负比例
    - away_ht_rate(3): 客队近10场半场胜平负比例
    - elo_diff_norm(1): (home_elo - away_elo) / 400
    - venue_type(1): 0=neutral, 1=home, -1=away
    - odds_implied(3): 赔率隐含概率(归一化)
    - league(4): 联赛one-hot
    """
    feats = [
        home_ht_rate.get("home", 0.40),
        home_ht_rate.get("draw", 0.30),
        home_ht_rate.get("away", 0.30),
        away_ht_rate.get("home", 0.30),
        away_ht_rate.get("draw", 0.30),
        away_ht_rate.get("away", 0.40),
        np.clip(elo_diff / 400.0, -1.0, 1.0),
        {"home": 1.0, "away": -1.0, "neutral": 0.0}.get(venue_type, 0.0),
    ]

    for sel in ["home", "draw", "away"]:
        odds_val = odds.get(sel, 2.0)
        feats.append(1.0 / max(odds_val, 1.01))

    feats.extend(LEAGUE_MAP.get(competition, [0, 0, 0, 0]))

    return np.array(feats[:INPUT_DIM], dtype=np.float32)


# ────────────────────────────
# Dataset & Model
# ────────────────────────────
class HalftimeDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray):
        self.features = torch.FloatTensor(features)
        self.labels = torch.LongTensor(labels)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


class HalftimeNet(nn.Module):
    """半场预测网络: input→64→32→3"""

    def __init__(self, input_dim: int = INPUT_DIM, hidden_dims: Tuple[int, ...] = (64, 32)):
        super().__init__()
        layers = []
        prev = input_dim
        for h in hidden_dims:
            layers.extend([
                nn.Linear(prev, h),
                nn.BatchNorm1d(h),
                nn.ReLU(),
                nn.Dropout(0.2),
            ])
            prev = h
        layers.append(nn.Linear(prev, OUTPUT_DIM))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_probs(self, features: np.ndarray) -> Dict[str, float]:
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            logits = self.forward(x)
            probs = torch.softmax(logits, dim=1).squeeze(0).numpy()
        return {"home": float(probs[0]), "draw": float(probs[1]), "away": float(probs[2])}


# ────────────────────────────
# Trainer
# ────────────────────────────
class HalftimeTrainer:
    def __init__(self) -> None:
        self.model = HalftimeNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        from database.models import SessionLocal, Match, MatchStatus

        session = SessionLocal()
        try:
            # 💡 强制按 kickoff_at 升序排列，防时序泄露
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.ht_home_goals.isnot(None),
                Match.ht_away_goals.isnot(None),
            ).order_by(Match.kickoff_at.asc()).all()

            if len(finished) < MIN_TRAIN_SAMPLES:
                logger.info(f"[halftime] 样本不足: {len(finished)}/{MIN_TRAIN_SAMPLES}")
                return None

            # 💡 滚动倾向统计：team_id -> {"win": 4, "draw": 3, "loss": 3, "total": 10}（初始默认平滑先验）
            team_ht_stats = {}

            features_list = []
            labels_list = []

            for match in finished:
                ht_home = match.ht_home_goals
                ht_away = match.ht_away_goals

                if ht_home > ht_away:
                    label = 0  # home
                elif ht_home == ht_away:
                    label = 1  # draw
                else:
                    label = 2  # away

                # 从滚动记录中获取当前时间点的主客队历史倾向概率
                h_stats = team_ht_stats.get(match.home_team_id, {"win": 4, "draw": 3, "loss": 3, "total": 10})
                a_stats = team_ht_stats.get(match.away_team_id, {"win": 4, "draw": 3, "loss": 3, "total": 10})

                home_ht_rate = {
                    "home": h_stats["win"] / h_stats["total"],
                    "draw": h_stats["draw"] / h_stats["total"],
                    "away": h_stats["loss"] / h_stats["total"]
                }
                away_ht_rate = {
                    "home": a_stats["win"] / a_stats["total"],
                    "draw": a_stats["draw"] / a_stats["total"],
                    "away": a_stats["loss"] / a_stats["total"]
                }

                elo_diff = 0.0
                if match.home_team and match.away_team:
                    h_elo = match.home_team.elo or 1500
                    a_elo = match.away_team.elo or 1500
                    elo_diff = h_elo - a_elo

                odds = {
                    "home": match.closing_odds_home or match.odds_home or 2.0,
                    "draw": match.closing_odds_draw or match.odds_draw or 3.0,
                    "away": match.closing_odds_away or match.odds_away or 2.0,
                }

                feats = extract_halftime_features(
                    home_ht_rate=home_ht_rate,
                    away_ht_rate=away_ht_rate,
                    elo_diff=elo_diff,
                    venue_type=match.venue_type or "neutral",
                    odds=odds,
                    competition=match.competition or "",
                )

                features_list.append(feats)
                labels_list.append(label)

                # 💡 关键：计算完当前特征后，把当场比赛的真实赛果滚动更新进主队的统计数据中（只对主队累加）
                h_id = match.home_team_id
                if h_id not in team_ht_stats:
                    team_ht_stats[h_id] = {"win": 4, "draw": 3, "loss": 3, "total": 10}
                
                team_ht_stats[h_id]["total"] += 1
                if label == 0:
                    team_ht_stats[h_id]["win"] += 1
                elif label == 1:
                    team_ht_stats[h_id]["draw"] += 1
                else:
                    team_ht_stats[h_id]["loss"] += 1

            if len(features_list) < MIN_TRAIN_SAMPLES:
                logger.info(f"[halftime] 有效样本不足: {len(features_list)}")
                return None

            features = np.stack(features_list)
            labels = np.array(labels_list, dtype=np.int64)

            self.feature_mean = features.mean(axis=0)
            self.feature_std = features.std(axis=0) + 1e-8
            features = (features - self.feature_mean) / self.feature_std

            logger.info(f"[halftime] 训练集: {len(features)} 样本, 分布: home={sum(labels==0)}, draw={sum(labels==1)}, away={sum(labels==2)}")
            return features, labels

        finally:
            session.close()

    def _team_ht_rate(self, session, team_id: int) -> Dict[str, float]:
        """计算某队历史半场胜平负比例(近似, 单队查询)"""
        if not team_id:
            return {"home": 0.40, "draw": 0.30, "away": 0.30}

        from database.models import Match, MatchStatus
        matches = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.ht_home_goals.isnot(None),
            Match.home_team_id == team_id,
        ).limit(20).all()

        if not matches:
            return {"home": 0.40, "draw": 0.30, "away": 0.30}

        h = d = a = 0
        for m in matches:
            if m.ht_home_goals > m.ht_away_goals:
                h += 1
            elif m.ht_home_goals == m.ht_away_goals:
                d += 1
            else:
                a += 1

        total = h + d + a
        return {"home": h / total, "draw": d / total, "away": a / total}

    def _batch_team_ht_rates(self, session) -> Dict[int, Dict[str, float]]:
        """批量查询所有球队的半场胜平负比例(1条SQL搞定)"""
        from database.models import Match, MatchStatus
        from sqlalchemy import func, case

        rows = session.query(
            Match.home_team_id,
            func.sum(case((Match.ht_home_goals > Match.ht_away_goals, 1), else_=0)).label("ht_home"),
            func.sum(case((Match.ht_home_goals == Match.ht_away_goals, 1), else_=0)).label("ht_draw"),
            func.sum(case((Match.ht_home_goals < Match.ht_away_goals, 1), else_=0)).label("ht_away"),
        ).filter(
            Match.status == MatchStatus.FINISHED,
            Match.ht_home_goals.isnot(None),
            Match.ht_away_goals.isnot(None),
        ).group_by(Match.home_team_id).all()

        result = {}
        for team_id, h, d, a in rows:
            total = (h or 0) + (d or 0) + (a or 0)
            if total > 0:
                result[team_id] = {"home": (h or 0) / total, "draw": (d or 0) / total, "away": (a or 0) / total}
            else:
                result[team_id] = {"home": 0.40, "draw": 0.30, "away": 0.30}
        return result

    def train(self) -> Optional[Dict]:
        data = self.build_training_data()
        if data is None:
            return None

        features, labels = data
        dataset = HalftimeDataset(features, labels)

        n = len(dataset)
        n_train = int(n * 0.8)
        n_val = n - n_train
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

        self.model = HalftimeNet()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)

        # 类别权重(平衡不均匀)
        class_counts = np.bincount(labels, minlength=OUTPUT_DIM)
        class_weights = 1.0 / (class_counts + 1)
        class_weights = class_weights / class_weights.sum() * OUTPUT_DIM
        weight_tensor = torch.FloatTensor(class_weights)

        best_val_acc = 0.0
        patience_counter = 0
        metrics = {"train_loss": [], "val_loss": [], "val_accuracy": []}

        for epoch in range(EPOCHS):
            self.model.train()
            train_loss = 0.0
            for batch_x, batch_y in train_loader:
                optimizer.zero_grad()
                logits = self.model(batch_x)
                loss = nn.functional.cross_entropy(logits, batch_y, weight=weight_tensor)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            train_loss /= len(train_loader)

            self.model.eval()
            val_loss = 0.0
            correct = 0
            total = 0
            with torch.no_grad():
                for batch_x, batch_y in val_loader:
                    logits = self.model(batch_x)
                    loss = nn.functional.cross_entropy(logits, batch_y)
                    val_loss += loss.item()
                    preds = logits.argmax(dim=1)
                    correct += (preds == batch_y).sum().item()
                    total += len(batch_y)

            val_loss /= len(val_loader)
            val_acc = correct / total if total > 0 else 0

            metrics["train_loss"].append(round(train_loss, 4))
            metrics["val_loss"].append(round(val_loss, 4))
            metrics["val_accuracy"].append(round(val_acc, 4))

            if val_acc > best_val_acc:
                best_val_acc = val_acc
                patience_counter = 0
                self._save_model()
            else:
                patience_counter += 1
                if patience_counter >= PATIENCE:
                    logger.info(f"[halftime] Early stop at epoch {epoch}")
                    break

            if epoch % 10 == 0:
                logger.info(f"[halftime] Epoch {epoch}: loss={train_loss:.4f}, val_acc={val_acc:.1%}")

        self._save_feature_stats()
        self._save_training_log(metrics)

        return {
            "epochs_trained": len(metrics["train_loss"]),
            "best_val_accuracy": round(best_val_acc, 4),
            "final_val_accuracy": metrics["val_accuracy"][-1] if metrics["val_accuracy"] else 0,
            "samples": n,
        }

    def _save_model(self) -> None:
        torch.save(self.model.state_dict(), MODEL_PATH)

    def _save_feature_stats(self) -> None:
        if self.feature_mean is not None:
            with open(FEATURE_STATS_PATH, "w") as f:
                json.dump({"mean": self.feature_mean.tolist(), "std": self.feature_std.tolist()}, f)

    def _save_training_log(self, metrics: Dict) -> None:
        with open(TRAINING_LOG_PATH, "w") as f:
            json.dump({"trained_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics}, f, indent=2)


# ────────────────────────────
# Predictor
# ────────────────────────────
class HalftimePredictor:
    """加载已训练模型，提供推理接口"""

    def __init__(self) -> None:
        self.model = HalftimeNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None
        self._load_model()

    def _load_model(self) -> None:
        if os.path.exists(MODEL_PATH):
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()
            logger.info("[halftime] Loaded trained model")

        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH, "r") as f:
                stats = json.load(f)
            self.feature_mean = np.array(stats["mean"], dtype=np.float32)
            self.feature_std = np.array(stats["std"], dtype=np.float32)

    def is_ready(self) -> bool:
        return os.path.exists(MODEL_PATH)

    def predict(
        self,
        home_ht_rate: Dict[str, float],
        away_ht_rate: Dict[str, float],
        elo_diff: float,
        venue_type: str,
        odds: Dict[str, float],
        competition: str,
    ) -> Dict[str, float]:
        raw_feats = extract_halftime_features(
            home_ht_rate, away_ht_rate, elo_diff, venue_type, odds, competition
        )
        feats = raw_feats
        if self.feature_mean is not None and self.feature_std is not None:
            feats = (raw_feats - self.feature_mean) / self.feature_std
        return self.model.predict_probs(feats)

    def predict_from_db(self, match_id: int) -> Optional[Dict]:
        from database.models import SessionLocal, Match

        session = SessionLocal()
        try:
            match = session.query(Match).filter(Match.id == match_id).first()
            if not match:
                return None

            trainer_temp = HalftimeTrainer()
            home_ht_rate = trainer_temp._team_ht_rate(session, match.home_team_id)
            away_ht_rate = trainer_temp._team_ht_rate(session, match.away_team_id)

            elo_diff = 0.0
            if match.home_team and match.away_team:
                elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

            odds = {
                "home": match.closing_odds_home or match.odds_home or 2.0,
                "draw": match.closing_odds_draw or match.odds_draw or 3.0,
                "away": match.closing_odds_away or match.odds_away or 2.0,
            }

            probs = self.predict(
                home_ht_rate=home_ht_rate,
                away_ht_rate=away_ht_rate,
                elo_diff=elo_diff,
                venue_type=match.venue_type or "neutral",
                odds=odds,
                competition=match.competition or "",
            )

            best = max(probs, key=probs.get)
            labels = {"home": "主胜", "draw": "平", "away": "客胜"}

            return {
                "match_id": match_id,
                "halftime_probs": probs,
                "recommended": best,
                "recommended_label": labels.get(best, best),
                "confidence": round(probs[best], 3),
                "model_version": "halftime_v1",
                "ready": True,
            }
        finally:
            session.close()

    def get_status(self) -> Dict:
        if not os.path.exists(TRAINING_LOG_PATH):
            return {"trained": False, "ready": False}
        try:
            with open(TRAINING_LOG_PATH, "r") as f:
                log = json.load(f)
            metrics = log.get("metrics", {})
            return {
                "trained": True,
                "ready": True,
                "trained_at": log.get("trained_at"),
                "final_val_accuracy": metrics.get("val_accuracy", [0])[-1] if metrics.get("val_accuracy") else 0,
                "epochs": len(metrics.get("train_loss", [])),
            }
        except (json.JSONDecodeError, IOError):
            return {"trained": False, "ready": False}


def halftime_train_job() -> None:
    """定时训练入口"""
    trainer = HalftimeTrainer()
    result = trainer.train()
    if result:
        logger.info(f"[halftime] Training done: acc={result['best_val_accuracy']:.1%}, samples={result['samples']}")
    else:
        logger.info("[halftime] Not enough data, skipping")
