"""
让球预测子模型 — MLP

特征: 让球盘口 + 主队近期覆盖率 + 客队近期覆盖率 + Elo差 + 赔率差 + 主客场 + 联赛
目标: 主覆盖(home) / 客覆盖(away) / 平盘(draw) 二分类(简化为上/下)
训练标签: 从比分+handicap推导

训练数据来源:
1. JingcaiIssueMatch.handicap + Match.actual_outcome → 直接推导
2. Match比分 + 推断handicap(从赔率差) → 扩充训练集
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from utils.logger import get_logger

logger = get_logger("sub_model_handicap")

MODEL_DIR = "./data/sub_models/handicap"
os.makedirs(MODEL_DIR, exist_ok=True)

MODEL_PATH = os.path.join(MODEL_DIR, "handicap_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "training_log.json")

INPUT_DIM = 13
# 3类: 主覆盖(home covers) / 平盘(draw/void) / 客覆盖(away covers)
OUTPUT_DIM = 3

BATCH_SIZE = 64
LEARNING_RATE = 1e-3
EPOCHS = 60
PATIENCE = 7
MIN_TRAIN_SAMPLES = 200

LEAGUE_MAP = {
    "EPL": [1, 0, 0, 0],
    "Bundesliga": [0, 1, 0, 0],
    "LaLiga": [0, 0, 1, 0],
    "SerieA": [0, 0, 0, 1],
}


# ────────────────────────────
# 让球结果推导
# ────────────────────────────
def handicap_outcome(home_goals: int, away_goals: int, handicap: int) -> str:
    """
    推导让球结果。
    handicap > 0: 主让handicap球（如+1=主让1球）
    handicap < 0: 客让|handicap|球
    handicap = 0: 标准盘
    """
    adjusted = (home_goals - handicap) - away_goals
    if adjusted > 0:
        return "home"  # 主覆盖
    elif adjusted == 0:
        return "draw"  # 平盘
    else:
        return "away"  # 客覆盖


def infer_handicap_from_odds(odds_home: float, odds_away: float) -> int:
    """从赔率差异推断隐含让球数"""
    if odds_home <= 1.01 or odds_away <= 1.01:
        return 0
    ratio = odds_away / odds_home
    if ratio > 1.8:
        return 1  # 主让1球
    elif ratio < 0.55:
        return -1  # 客让1球
    elif ratio > 2.5:
        return 2  # 主让2球
    elif ratio < 0.4:
        return -2  # 客让2球
    return 0


# ────────────────────────────
# 特征工程
# ────────────────────────────
def extract_handicap_features(
    handicap: int,
    home_cover_rate: float,
    away_cover_rate: float,
    elo_diff: float,
    odds_diff: float,
    venue_type: str,
    competition: str,
) -> np.ndarray:
    """
    14维特征:
    - handicap_norm(1): handicap / 2
    - home_cover_rate(1): 主队覆盖率
    - away_cover_rate(1): 客队覆盖率
    - elo_diff_norm(1)
    - odds_diff(1): (1/odds_home - 1/odds_away)
    - venue_type(1)
    - league(4)
    - home_goal_diff(1): 主队净胜球均值
    - away_goal_diff(1): 客队净胜球均值
    - odds_ratio(1): odds_away/odds_home
    """
    feats = [
        np.clip(handicap / 2.0, -1.0, 1.0),
        home_cover_rate,
        away_cover_rate,
        np.clip(elo_diff / 400.0, -1.0, 1.0),
        np.clip(odds_diff, -0.5, 0.5),
        {"home": 1.0, "away": -1.0, "neutral": 0.0}.get(venue_type, 0.0),
    ]
    feats.extend(LEAGUE_MAP.get(competition, [0, 0, 0, 0]))
    feats.extend([
        0.0,  # placeholder: home_goal_diff (从DB填充)
        0.0,  # placeholder: away_goal_diff
        0.0,  # placeholder: odds_ratio
    ])
    return np.array(feats[:INPUT_DIM], dtype=np.float32)


# ────────────────────────────
# Dataset & Model
# ────────────────────────────
class HandicapDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray):
        self.features = torch.FloatTensor(features)
        self.labels = torch.LongTensor(labels)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


class HandicapNet(nn.Module):
    """让球预测网络: input→64→32→3"""

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
class HandicapTrainer:
    def __init__(self) -> None:
        self.model = HandicapNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        from database.models import SessionLocal, Match, MatchStatus, JingcaiIssueMatch

        session = SessionLocal()
        try:
            # 来源1: 竞彩有明确handicap的比赛
            jm_matches = session.query(JingcaiIssueMatch).filter(
                JingcaiIssueMatch.handicap.isnot(None),
            ).all()

            # 来源2: 所有有比分和赔率的已结束比赛(推断handicap)
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None),
                Match.actual_away_goals.isnot(None),
                Match.closing_odds_home.isnot(None),
                Match.closing_odds_home > 1.01,
            ).all()

            # 构建竞彩match→handicap映射
            jingcai_handicap = {}
            for jm in jm_matches:
                jingcai_handicap[jm.match_id] = jm.handicap

            # 批量预计算球队覆盖率和净胜球(避免N+1)
            team_cover_rates = self._batch_team_cover_rates(session)
            team_goal_diffs = self._batch_team_goal_diffs(session)

            features_list = []
            labels_list = []

            for match in finished:
                # 确定handicap
                if match.id in jingcai_handicap:
                    handicap = jingcai_handicap[match.id]
                else:
                    # 从赔率推断
                    oh = match.closing_odds_home or 2.0
                    oa = match.closing_odds_away or 2.0
                    handicap = infer_handicap_from_odds(oh, oa)

                # 推导让球结果
                outcome = handicap_outcome(
                    match.actual_home_goals, match.actual_away_goals, handicap
                )
                label_map = {"home": 0, "draw": 1, "away": 2}
                label = label_map[outcome]

                # 球队数据
                home_cover = team_cover_rates.get(match.home_team_id, 0.5)
                away_cover = team_cover_rates.get(match.away_team_id, 0.5)

                elo_diff = 0.0
                if match.home_team and match.away_team:
                    elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

                oh = match.closing_odds_home or 2.0
                od = match.closing_odds_draw or 3.0
                oa = match.closing_odds_away or 2.0
                odds_diff = (1.0 / max(oh, 1.01)) - (1.0 / max(oa, 1.01))

                # 球队净胜球
                home_gd = team_goal_diffs.get(match.home_team_id, 0.0)
                away_gd = team_goal_diffs.get(match.away_team_id, 0.0)

                feats = extract_handicap_features(
                    handicap=handicap,
                    home_cover_rate=home_cover,
                    away_cover_rate=away_cover,
                    elo_diff=elo_diff,
                    odds_diff=odds_diff,
                    venue_type=match.venue_type or "neutral",
                    competition=match.competition or "",
                )
                # 填充后3维
                feats[10] = np.clip(home_gd / 3.0, -1.0, 1.0)
                feats[11] = np.clip(away_gd / 3.0, -1.0, 1.0)
                feats[12] = np.clip(oa / max(oh, 0.01), 0, 5) / 5.0

                features_list.append(feats)
                labels_list.append(label)

            if len(features_list) < MIN_TRAIN_SAMPLES:
                logger.info(f"[handicap] 有效样本不足: {len(features_list)}")
                return None

            features = np.stack(features_list)
            labels = np.array(labels_list, dtype=np.int64)

            self.feature_mean = features.mean(axis=0)
            self.feature_std = features.std(axis=0) + 1e-8
            features = (features - self.feature_mean) / self.feature_std

            dist = {"home": sum(labels == 0), "draw": sum(labels == 1), "away": sum(labels == 2)}
            logger.info(f"[handicap] 训练集: {len(features)} 样本, 分布: {dist}")
            return features, labels

        finally:
            session.close()

    def _team_cover_rate(self, session, team_id: int) -> float:
        """计算球队覆盖率(近似)"""
        if not team_id:
            return 0.5

        from database.models import Match, MatchStatus
        matches = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.home_team_id == team_id,
        ).limit(20).all()

        if not matches:
            return 0.5

        cover = 0
        total = 0
        for m in matches:
            oh = m.closing_odds_home or m.odds_home or 2.0
            oa = m.closing_odds_away or m.odds_away or 2.0
            hc = infer_handicap_from_odds(oh, oa)
            result = handicap_outcome(m.actual_home_goals, m.actual_away_goals, hc)
            total += 1
            if result == "home":
                cover += 1

        return cover / total if total > 0 else 0.5

    def _team_goal_diff(self, session, team_id: int) -> float:
        """球队场均净胜球"""
        if not team_id:
            return 0.0

        from database.models import Match, MatchStatus
        matches = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.home_team_id == team_id,
        ).limit(20).all()

        if not matches:
            return 0.0

        diffs = [(m.actual_home_goals - m.actual_away_goals) for m in matches]
        return sum(diffs) / len(diffs) if diffs else 0.0

    def _batch_team_cover_rates(self, session) -> Dict[int, float]:
        """批量查询所有球队覆盖率(1条SQL)"""
        from database.models import Match, MatchStatus
        from sqlalchemy import func, case

        # 按home_team_id分组，计算home胜的场次
        rows = session.query(
            Match.home_team_id,
            func.count().label("total"),
            func.sum(case((Match.actual_home_goals > Match.actual_away_goals, 1), else_=0)).label("wins"),
        ).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.closing_odds_home.isnot(None),
        ).group_by(Match.home_team_id).all()

        return {r[0]: (r[2] / r[1] if r[1] > 0 else 0.5) for r in rows}

    def _batch_team_goal_diffs(self, session) -> Dict[int, float]:
        """批量查询所有球队场均净胜球(1条SQL)"""
        from database.models import Match, MatchStatus
        from sqlalchemy import func

        rows = session.query(
            Match.home_team_id,
            func.avg(Match.actual_home_goals - Match.actual_away_goals).label("gd"),
        ).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.actual_away_goals.isnot(None),
        ).group_by(Match.home_team_id).all()

        return {r[0]: float(r[1] or 0.0) for r in rows}

    def train(self) -> Optional[Dict]:
        data = self.build_training_data()
        if data is None:
            return None

        features, labels = data
        dataset = HandicapDataset(features, labels)

        n = len(dataset)
        n_train = int(n * 0.8)
        n_val = n - n_train
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

        self.model = HandicapNet()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)

        class_counts = np.bincount(labels, minlength=OUTPUT_DIM).astype(float)
        class_counts[class_counts == 0] = 1
        class_weights = 1.0 / class_counts
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
                    logger.info(f"[handicap] Early stop at epoch {epoch}")
                    break

            if epoch % 10 == 0:
                logger.info(f"[handicap] Epoch {epoch}: loss={train_loss:.4f}, val_acc={val_acc:.1%}")

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
class HandicapPredictor:
    def __init__(self) -> None:
        self.model = HandicapNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None
        self._load_model()

    def _load_model(self) -> None:
        if os.path.exists(MODEL_PATH):
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()
            logger.info("[handicap] Loaded trained model")

        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH, "r") as f:
                stats = json.load(f)
            self.feature_mean = np.array(stats["mean"], dtype=np.float32)
            self.feature_std = np.array(stats["std"], dtype=np.float32)

    def is_ready(self) -> bool:
        return os.path.exists(MODEL_PATH)

    def predict(
        self,
        handicap: int,
        home_cover_rate: float,
        away_cover_rate: float,
        elo_diff: float,
        odds_diff: float,
        venue_type: str,
        competition: str,
        home_gd: float = 0.0,
        away_gd: float = 0.0,
        odds_ratio: float = 1.0,
    ) -> Dict[str, float]:
        raw_feats = extract_handicap_features(
            handicap, home_cover_rate, away_cover_rate, elo_diff, odds_diff, venue_type, competition,
        )
        raw_feats[10] = np.clip(home_gd / 3.0, -1.0, 1.0)
        raw_feats[11] = np.clip(away_gd / 3.0, -1.0, 1.0)
        raw_feats[12] = np.clip(odds_ratio, 0, 5) / 5.0

        feats = raw_feats
        if self.feature_mean is not None and self.feature_std is not None:
            feats = (raw_feats - self.feature_mean) / self.feature_std
        return self.model.predict_probs(feats)

    def predict_from_db(self, match_id: int, handicap: Optional[int] = None) -> Optional[Dict]:
        from database.models import SessionLocal, Match, JingcaiIssueMatch

        session = SessionLocal()
        try:
            match = session.query(Match).filter(Match.id == match_id).first()
            if not match:
                return None

            # 确定handicap
            if handicap is None:
                jm = session.query(JingcaiIssueMatch).filter(
                    JingcaiIssueMatch.match_id == match_id,
                ).first()
                if jm and jm.handicap is not None:
                    handicap = jm.handicap
                else:
                    oh = match.closing_odds_home or match.odds_home or 2.0
                    oa = match.closing_odds_away or match.odds_away or 2.0
                    handicap = infer_handicap_from_odds(oh, oa)

            trainer_temp = HandicapTrainer()
            home_cover = trainer_temp._team_cover_rate(session, match.home_team_id)
            away_cover = trainer_temp._team_cover_rate(session, match.away_team_id)
            home_gd = trainer_temp._team_goal_diff(session, match.home_team_id)
            away_gd = trainer_temp._team_goal_diff(session, match.away_team_id)

            elo_diff = 0.0
            if match.home_team and match.away_team:
                elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

            oh = match.closing_odds_home or match.odds_home or 2.0
            oa = match.closing_odds_away or match.odds_away or 2.0
            odds_diff = (1.0 / max(oh, 1.01)) - (1.0 / max(oa, 1.01))
            odds_ratio = oa / max(oh, 0.01)

            probs = self.predict(
                handicap=handicap,
                home_cover_rate=home_cover,
                away_cover_rate=away_cover,
                elo_diff=elo_diff,
                odds_diff=odds_diff,
                venue_type=match.venue_type or "neutral",
                competition=match.competition or "",
                home_gd=home_gd,
                away_gd=away_gd,
                odds_ratio=odds_ratio,
            )

            best = max(probs, key=probs.get)
            labels = {"home": "主覆盖", "draw": "平盘", "away": "客覆盖"}

            return {
                "match_id": match_id,
                "handicap": handicap,
                "handicap_probs": probs,
                "recommended": best,
                "recommended_label": labels.get(best, best),
                "confidence": round(probs[best], 3),
                "model_version": "handicap_v1",
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


def handicap_train_job() -> None:
    trainer = HandicapTrainer()
    result = trainer.train()
    if result:
        logger.info(f"[handicap] Training done: acc={result['best_val_accuracy']:.1%}, samples={result['samples']}")
    else:
        logger.info("[handicap] Not enough data, skipping")
