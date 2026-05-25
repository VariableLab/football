"""
比分预测子模型 — MLP

特征: 主队进球效率 + 客队失球效率 + 主客场修正 + Elo差 + 赔率隐含概率 + 联赛
目标: 比分多分类(约30个常见比分), 输出top3概率
训练标签: actual_home_goals:actual_away_goals

独立训练，与主模型共享赛事类型权重概念。
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from utils.logger import get_logger

logger = get_logger("sub_model_score")

MODEL_DIR = "./data/sub_models/score"
os.makedirs(MODEL_DIR, exist_ok=True)

MODEL_PATH = os.path.join(MODEL_DIR, "score_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "training_log.json")
SCORE_MAP_PATH = os.path.join(MODEL_DIR, "score_map.json")

# 只保留覆盖95%+比赛的常见比分
COMMON_SCORES = [
    "0:0", "1:0", "0:1", "1:1", "2:0", "0:2", "2:1", "1:2",
    "2:2", "3:0", "0:3", "3:1", "1:3", "3:2", "2:3",
    "3:3", "4:0", "0:4", "4:1", "1:4", "4:2", "2:4",
    "4:3", "3:4", "5:0", "0:5", "5:1", "1:5",
    "5:2", "2:5",
]
SCORE_TO_IDX = {s: i for i, s in enumerate(COMMON_SCORES)}
IDX_TO_SCORE = {i: s for s, i in SCORE_TO_IDX.items()}
NUM_CLASSES = len(COMMON_SCORES)

INPUT_DIM = 13
BATCH_SIZE = 64
LEARNING_RATE = 1e-3
EPOCHS = 60
PATIENCE = 7
MIN_TRAIN_SAMPLES = 500

LEAGUE_MAP = {
    "EPL": [1, 0, 0, 0],
    "Bundesliga": [0, 1, 0, 0],
    "LaLiga": [0, 0, 1, 0],
    "SerieA": [0, 0, 0, 1],
}


# ────────────────────────────
# 特征工程
# ────────────────────────────
def extract_score_features(
    home_goals_avg: float,
    home_concede_avg: float,
    away_goals_avg: float,
    away_concede_avg: float,
    elo_diff: float,
    venue_type: str,
    odds: Dict[str, float],
    competition: str,
) -> np.ndarray:
    """
    15维特征:
    - home_goals_avg(1): 主队场均进球
    - home_concede_avg(1): 主队场均失球
    - away_goals_avg(1): 客队场均进球
    - away_concede_avg(1): 客队场均失球
    - elo_diff_norm(1)
    - venue_type(1)
    - odds_implied(3)
    - league(4)
    """
    feats = [
        np.clip(home_goals_avg / 3.0, 0, 1),
        np.clip(home_concede_avg / 3.0, 0, 1),
        np.clip(away_goals_avg / 3.0, 0, 1),
        np.clip(away_concede_avg / 3.0, 0, 1),
        np.clip(elo_diff / 400.0, -1.0, 1.0),
        {"home": 1.0, "away": -1.0, "neutral": 0.0}.get(venue_type, 0.0),
    ]

    for sel in ["home", "draw", "away"]:
        feats.append(1.0 / max(odds.get(sel, 2.0), 1.01))

    feats.extend(LEAGUE_MAP.get(competition, [0, 0, 0, 0]))

    return np.array(feats[:INPUT_DIM], dtype=np.float32)


def score_to_label(home_goals: int, away_goals: int) -> int:
    """比分→类别索引, 不在COMMON_SCORES中→0:0(最保守)"""
    key = f"{home_goals}:{away_goals}"
    return SCORE_TO_IDX.get(key, 0)


def label_to_score(label: int) -> str:
    return IDX_TO_SCORE.get(label, "0:0")


# ────────────────────────────
# Dataset & Model
# ────────────────────────────
class ScoreDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray):
        self.features = torch.FloatTensor(features)
        self.labels = torch.LongTensor(labels)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


class ScoreNet(nn.Module):
    """比分预测网络: input→128→64→32→30"""

    def __init__(self, input_dim: int = INPUT_DIM, hidden_dims: Tuple[int, ...] = (128, 64, 32)):
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
        layers.append(nn.Linear(prev, NUM_CLASSES))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_top3(self, features: np.ndarray) -> List[Dict[str, float]]:
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            logits = self.forward(x)
            probs = torch.softmax(logits, dim=1).squeeze(0).numpy()

        top3_idx = np.argsort(probs)[-3:][::-1]
        return [
            {"score": label_to_score(int(i)), "probability": round(float(probs[i]), 4)}
            for i in top3_idx
        ]


# ────────────────────────────
# Trainer
# ────────────────────────────
class ScoreTrainer:
    def __init__(self) -> None:
        self.model = ScoreNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        from database.models import SessionLocal, Match, MatchStatus, Team
        from sqlalchemy import func

        session = SessionLocal()
        try:
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None),
                Match.actual_away_goals.isnot(None),
            ).all()

            if len(finished) < MIN_TRAIN_SAMPLES:
                logger.info(f"[score] 样本不足: {len(finished)}/{MIN_TRAIN_SAMPLES}")
                return None

            # 批量预计算球队进球/失球统计(避免N+1)
            team_stats = self._batch_team_goal_stats(session)

            features_list = []
            labels_list = []

            for match in finished:
                label = score_to_label(match.actual_home_goals, match.actual_away_goals)

                home_stats = team_stats.get(match.home_team_id, {"goals_avg": 1.3, "concede_avg": 1.2})
                away_stats = team_stats.get(match.away_team_id, {"goals_avg": 1.3, "concede_avg": 1.2})

                elo_diff = 0.0
                if match.home_team and match.away_team:
                    elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

                odds = {
                    "home": match.closing_odds_home or match.odds_home or 2.0,
                    "draw": match.closing_odds_draw or match.odds_draw or 3.0,
                    "away": match.closing_odds_away or match.odds_away or 2.0,
                }

                feats = extract_score_features(
                    home_goals_avg=home_stats["goals_avg"],
                    home_concede_avg=home_stats["concede_avg"],
                    away_goals_avg=away_stats["goals_avg"],
                    away_concede_avg=away_stats["concede_avg"],
                    elo_diff=elo_diff,
                    venue_type=match.venue_type or "neutral",
                    odds=odds,
                    competition=match.competition or "",
                )

                features_list.append(feats)
                labels_list.append(label)

            if len(features_list) < MIN_TRAIN_SAMPLES:
                return None

            features = np.stack(features_list)
            labels = np.array(labels_list, dtype=np.int64)

            self.feature_mean = features.mean(axis=0)
            self.feature_std = features.std(axis=0) + 1e-8
            features = (features - self.feature_mean) / self.feature_std

            # 保存比分映射
            with open(SCORE_MAP_PATH, "w") as f:
                json.dump({"scores": COMMON_SCORES, "num_classes": NUM_CLASSES}, f)

            logger.info(f"[score] 训练集: {len(features)} 样本")
            return features, labels

        finally:
            session.close()

    def _team_goal_stats(self, session, team_id: int) -> Dict[str, float]:
        from database.models import Match, MatchStatus

        if not team_id:
            return {"goals_avg": 1.3, "concede_avg": 1.2}

        matches = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.home_team_id == team_id,
        ).limit(20).all()

        if not matches:
            return {"goals_avg": 1.3, "concede_avg": 1.2}

        goals = [m.actual_home_goals for m in matches if m.actual_home_goals is not None]
        concede = [m.actual_away_goals for m in matches if m.actual_away_goals is not None]

        return {
            "goals_avg": sum(goals) / len(goals) if goals else 1.3,
            "concede_avg": sum(concede) / len(concede) if concede else 1.2,
        }

    def _batch_team_goal_stats(self, session) -> Dict[int, Dict[str, float]]:
        """批量查询所有球队的进球/失球统计"""
        from database.models import Match, MatchStatus
        from sqlalchemy import func

        rows = session.query(
            Match.home_team_id,
            func.avg(Match.actual_home_goals).label("goals_avg"),
            func.avg(Match.actual_away_goals).label("concede_avg"),
        ).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.actual_away_goals.isnot(None),
        ).group_by(Match.home_team_id).all()

        return {
            r[0]: {"goals_avg": float(r[1] or 1.3), "concede_avg": float(r[2] or 1.2)}
            for r in rows
        }

    def train(self) -> Optional[Dict]:
        data = self.build_training_data()
        if data is None:
            return None

        features, labels = data
        dataset = ScoreDataset(features, labels)

        n = len(dataset)
        n_train = int(n * 0.8)
        n_val = n - n_train
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

        self.model = ScoreNet()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)

        # 类别权重
        class_counts = np.bincount(labels, minlength=NUM_CLASSES).astype(float)
        class_counts[class_counts == 0] = 1
        class_weights = 1.0 / class_counts
        class_weights = class_weights / class_weights.sum() * NUM_CLASSES
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
                    logger.info(f"[score] Early stop at epoch {epoch}")
                    break

            if epoch % 10 == 0:
                logger.info(f"[score] Epoch {epoch}: loss={train_loss:.4f}, val_acc={val_acc:.1%}")

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
class ScorePredictor:
    def __init__(self) -> None:
        self.model = ScoreNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None
        self._load_model()

    def _load_model(self) -> None:
        if os.path.exists(MODEL_PATH):
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()
            logger.info("[score] Loaded trained model")

        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH, "r") as f:
                stats = json.load(f)
            self.feature_mean = np.array(stats["mean"], dtype=np.float32)
            self.feature_std = np.array(stats["std"], dtype=np.float32)

    def is_ready(self) -> bool:
        return os.path.exists(MODEL_PATH)

    def predict(
        self,
        home_goals_avg: float,
        home_concede_avg: float,
        away_goals_avg: float,
        away_concede_avg: float,
        elo_diff: float,
        venue_type: str,
        odds: Dict[str, float],
        competition: str,
    ) -> List[Dict[str, float]]:
        raw_feats = extract_score_features(
            home_goals_avg, home_concede_avg, away_goals_avg, away_concede_avg,
            elo_diff, venue_type, odds, competition,
        )
        feats = raw_feats
        if self.feature_mean is not None and self.feature_std is not None:
            feats = (raw_feats - self.feature_mean) / self.feature_std
        return self.model.predict_top3(feats)

    def predict_from_db(self, match_id: int) -> Optional[Dict]:
        from database.models import SessionLocal, Match

        session = SessionLocal()
        try:
            match = session.query(Match).filter(Match.id == match_id).first()
            if not match:
                return None

            trainer_temp = ScoreTrainer()
            home_stats = trainer_temp._team_goal_stats(session, match.home_team_id)
            away_stats = trainer_temp._team_goal_stats(session, match.away_team_id)

            elo_diff = 0.0
            if match.home_team and match.away_team:
                elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

            odds = {
                "home": match.closing_odds_home or match.odds_home or 2.0,
                "draw": match.closing_odds_draw or match.odds_draw or 3.0,
                "away": match.closing_odds_away or match.odds_away or 2.0,
            }

            top3 = self.predict(
                home_goals_avg=home_stats["goals_avg"],
                home_concede_avg=home_stats["concede_avg"],
                away_goals_avg=away_stats["goals_avg"],
                away_concede_avg=away_stats["concede_avg"],
                elo_diff=elo_diff,
                venue_type=match.venue_type or "neutral",
                odds=odds,
                competition=match.competition or "",
            )

            return {
                "match_id": match_id,
                "top3_scores": top3,
                "model_version": "score_v1",
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


def score_train_job() -> None:
    trainer = ScoreTrainer()
    result = trainer.train()
    if result:
        logger.info(f"[score] Training done: acc={result['best_val_accuracy']:.1%}, samples={result['samples']}")
    else:
        logger.info("[score] Not enough data, skipping")
