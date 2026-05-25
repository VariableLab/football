"""
Bet Neural Network — 独立预测学习系统

架构:
- BetNet: 3层MLP (input→64→32→16→output)
- 输入: 模型预测(SPF3 + RQ3 + 比分top3) + 赔率(3) + Elo差(1) + 赔率变动(3) + 联赛类型(4) = 20维
- 输出: 每个选项(home/draw/away)的预测评分(0-1)
- 训练标签: 实际结果one-hot，用加权BCE损失(高概率预测失误权重大)
- 闭环: 比赛结果录入 → 构建训练集 → 增量训练 → 更新策略建议

独立运行，不依赖主预测引擎的内部逻辑。
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from logger import get_logger
from alert_manager import fire_alert

logger = get_logger("bet_nn")

MODEL_DIR = "./data/bet_nn"
os.makedirs(MODEL_DIR, exist_ok=True)

MODEL_PATH = os.path.join(MODEL_DIR, "bet_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "training_log.json")

# 特征维度
INPUT_DIM = 20
OUTPUT_DIM = 3  # home / draw / away

# 训练超参
BATCH_SIZE = 64
LEARNING_RATE = 1e-3
EPOCHS = 50
PATIENCE = 5  # early stopping
MIN_TRAIN_SAMPLES = 50


# ────────────────────────────
# 特征工程
# ────────────────────────────
LEAGUE_MAP = {
    "EPL": [1, 0, 0, 0],
    "Bundesliga": [0, 1, 0, 0],
    "LaLiga": [0, 0, 1, 0],
    "SerieA": [0, 0, 0, 1],
    # 其余联赛 → [0,0,0,0]
}


def extract_features(
    spf_probs: Dict[str, float],
    rq_probs: Dict[str, float],
    score_top3: Dict[str, float],
    odds: Dict[str, float],
    elo_diff: float,
    odds_movement: Dict[str, float],
    competition: str,
) -> np.ndarray:
    """从预测结果提取20维特征向量"""
    feats = [
        spf_probs.get("home", 0.33),
        spf_probs.get("draw", 0.33),
        spf_probs.get("away", 0.33),
        rq_probs.get("home", 0.33),
        rq_probs.get("draw", 0.33),
        rq_probs.get("away", 0.33),
    ]

    # 比分top3概率（按概率排序取前3）
    top_scores = sorted(score_top3.items(), key=lambda x: -x[1])[:3]
    for _, p in top_scores:
        feats.append(p)
    while len(feats) < 9:
        feats.append(0.0)

    # 赔率（归一化到概率空间）
    for sel in ["home", "draw", "away"]:
        odds_val = odds.get(sel, 2.0)
        feats.append(1.0 / max(odds_val, 1.01))

    # Elo差（归一化）
    feats.append(np.clip(elo_diff / 400.0, -1.0, 1.0))

    # 赔率变动（百分比变化）
    for sel in ["home", "draw", "away"]:
        feats.append(np.clip(odds_movement.get(sel, 0.0) / 0.1, -1.0, 1.0))

    # 联赛类型 one-hot
    feats.extend(LEAGUE_MAP.get(competition, [0, 0, 0, 0]))

    return np.array(feats[:INPUT_DIM], dtype=np.float32)


# ────────────────────────────
# Dataset
# ────────────────────────────
class BetDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray, weights: np.ndarray):
        self.features = torch.FloatTensor(features)
        self.labels = torch.FloatTensor(labels)
        self.weights = torch.FloatTensor(weights)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, ...]:
        return self.features[idx], self.labels[idx], self.weights[idx]


# ────────────────────────────
# Model
# ────────────────────────────
class BetNet(nn.Module):
    """
    预测评分网络

    输入: 20维特征(模型预测 + 赔率 + Elo + 变动 + 联赛)
    输出: 3维(home/draw/away)预测评分
    """
    def __init__(self, input_dim: int = INPUT_DIM, hidden_dims: Tuple[int, ...] = (64, 32, 16)):
        super().__init__()
        layers = []
        prev_dim = input_dim
        for h_dim in hidden_dims:
            layers.extend([
                nn.Linear(prev_dim, h_dim),
                nn.BatchNorm1d(h_dim),
                nn.ReLU(),
                nn.Dropout(0.2),
            ])
            prev_dim = h_dim
        layers.append(nn.Linear(prev_dim, OUTPUT_DIM))
        layers.append(nn.Sigmoid())
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_value(self, features: np.ndarray) -> Dict[str, float]:
        """单场推理：返回home/draw/away的预测评分"""
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            out = self.forward(x).squeeze(0).numpy()
        return {"home": float(out[0]), "draw": float(out[1]), "away": float(out[2])}


# ────────────────────────────
# Trainer
# ────────────────────────────
class BetNetTrainer:
    """训练管理器：构建数据集 → 训练 → 保存模型"""

    def __init__(self) -> None:
        self.model = BetNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """从数据库构建训练数据（批量查询，避免N+1）"""
        from models import SessionLocal, Match, MatchStatus, Prediction

        session = SessionLocal()
        try:
            # 只取有收盘赔率的已结束比赛（避免无赔率的噪声）
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.closing_odds_home != None,
                Match.closing_odds_home > 1.01,
            ).all()

            if len(finished) < MIN_TRAIN_SAMPLES:
                logger.info(f"[bet-nn] 训练样本不足: {len(finished)}/{MIN_TRAIN_SAMPLES}")
                return None

            # 批量获取所有预测
            match_ids = [m.id for m in finished]
            all_preds = session.query(Prediction).filter(
                Prediction.match_id.in_(match_ids),
            ).all()

            # 按match_id和play_type索引
            pred_map = {}
            for p in all_preds:
                key = (p.match_id, p.play_type)
                probs = p.probabilities if isinstance(p.probabilities, dict) else json.loads(p.probabilities) if p.probabilities else {}
                pred_map[key] = probs

            features_list = []
            labels_list = []
            weights_list = []

            for match in finished:
                spf = pred_map.get((match.id, "spf"))
                if not spf:
                    continue

                score_probs = pred_map.get((match.id, "score"), {})

                odds = {
                    "home": match.closing_odds_home or 2.0,
                    "draw": match.closing_odds_draw or 3.0,
                    "away": match.closing_odds_away or 2.0,
                }

                elo_diff = 0.0

                odds_movement = {}
                for sel in ["home", "draw", "away"]:
                    closing = getattr(match, f"closing_odds_{sel}", None) or 0
                    opening = getattr(match, f"opening_odds_{sel}", None) or 0
                    if closing and opening:
                        odds_movement[sel] = (closing - opening) / opening
                    else:
                        odds_movement[sel] = 0.0

                feats = extract_features(
                    spf_probs=spf,
                    rq_probs=spf,
                    score_top3=score_probs or spf,
                    odds=odds,
                    elo_diff=elo_diff,
                    odds_movement=odds_movement,
                    competition=match.competition or "",
                )

                outcome_idx = {"home": 0, "draw": 1, "away": 2}
                label = np.zeros(OUTPUT_DIM, dtype=np.float32)
                idx = outcome_idx.get(match.actual_outcome, 1)
                label[idx] = 1.0

                max_prob = max(spf.values()) if spf else 0.33
                predicted = max(spf, key=spf.get) if spf else "draw"
                correct = predicted == match.actual_outcome
                if max_prob >= 0.50 and not correct:
                    weight = 3.0
                elif max_prob >= 0.40 and not correct:
                    weight = 2.0
                else:
                    weight = 1.0

                features_list.append(feats)
                labels_list.append(label)
                weights_list.append(weight)

            if len(features_list) < MIN_TRAIN_SAMPLES:
                logger.info(f"[bet-nn] 有效训练样本不足: {len(features_list)}")
                return None

            features = np.stack(features_list)
            labels = np.stack(labels_list)
            weights = np.array(weights_list, dtype=np.float32)

            self.feature_mean = features.mean(axis=0)
            self.feature_std = features.std(axis=0) + 1e-8
            features = (features - self.feature_mean) / self.feature_std

            logger.info(f"[bet-nn] 训练集: {len(features)} 样本")
            return features, labels, weights

        finally:
            session.close()

    def train(self) -> Optional[Dict]:
        """训练模型，返回训练指标"""
        data = self.build_training_data()
        if data is None:
            return None

        features, labels, weights = data
        dataset = BetDataset(features, labels, weights)

        # 80/20 split
        n = len(dataset)
        n_train = int(n * 0.8)
        n_val = n - n_train
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

        self.model = BetNet()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)

        best_val_loss = float("inf")
        patience_counter = 0
        metrics = {"train_loss": [], "val_loss": [], "val_accuracy": []}

        for epoch in range(EPOCHS):
            # Training
            self.model.train()
            train_loss = 0.0
            for batch_features, batch_labels, batch_weights in train_loader:
                optimizer.zero_grad()
                outputs = self.model(batch_features)
                # 加权BCE损失
                loss_per_sample = nn.functional.binary_cross_entropy(outputs, batch_labels, reduction="none")
                weighted_loss = (loss_per_sample * batch_weights.unsqueeze(1)).mean()
                weighted_loss.backward()
                optimizer.step()
                train_loss += weighted_loss.item()

            train_loss /= len(train_loader)

            # Validation
            self.model.eval()
            val_loss = 0.0
            correct = 0
            total = 0
            with torch.no_grad():
                for batch_features, batch_labels, batch_weights in val_loader:
                    outputs = self.model(batch_features)
                    loss = nn.functional.binary_cross_entropy(outputs, batch_labels, reduction="mean")
                    val_loss += loss.item()
                    preds = outputs.argmax(dim=1)
                    actuals = batch_labels.argmax(dim=1)
                    correct += (preds == actuals).sum().item()
                    total += len(actuals)

            val_loss /= len(val_loader)
            val_acc = correct / total if total > 0 else 0

            metrics["train_loss"].append(round(train_loss, 4))
            metrics["val_loss"].append(round(val_loss, 4))
            metrics["val_accuracy"].append(round(val_acc, 4))

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                self._save_model()
            else:
                patience_counter += 1
                if patience_counter >= PATIENCE:
                    logger.info(f"[bet-nn] Early stop at epoch {epoch}")
                    break

            if epoch % 10 == 0 or epoch == EPOCHS - 1:
                logger.info(
                    f"[bet-nn] Epoch {epoch}: train_loss={train_loss:.4f}, "
                    f"val_loss={val_loss:.4f}, val_acc={val_acc:.1%}"
                )

        # 保存特征统计
        self._save_feature_stats()
        self._save_training_log(metrics)

        return {
            "epochs_trained": len(metrics["train_loss"]),
            "best_val_loss": round(best_val_loss, 4),
            "final_val_accuracy": metrics["val_accuracy"][-1] if metrics["val_accuracy"] else 0,
            "samples": n,
        }

    def _save_model(self) -> None:
        torch.save(self.model.state_dict(), MODEL_PATH)
        logger.info(f"[bet-nn] Model saved to {MODEL_PATH}")

    def _save_feature_stats(self) -> None:
        if self.feature_mean is not None:
            stats = {
                "mean": self.feature_mean.tolist(),
                "std": self.feature_std.tolist(),
            }
            with open(FEATURE_STATS_PATH, "w") as f:
                json.dump(stats, f)

    def _save_training_log(self, metrics: Dict) -> None:
        log = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        }
        with open(TRAINING_LOG_PATH, "w") as f:
            json.dump(log, f, indent=2)


# ────────────────────────────
# Loader — 加载已训练模型做推理
# ────────────────────────────
class BetNetPredictor:
    """加载已训练模型，提供推理接口"""

    def __init__(self) -> None:
        self.model = BetNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None
        self._load_model()

    def _load_model(self) -> None:
        if os.path.exists(MODEL_PATH):
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()
            logger.info("[bet-nn] Loaded trained model")

        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH, "r") as f:
                stats = json.load(f)
            self.feature_mean = np.array(stats["mean"], dtype=np.float32)
            self.feature_std = np.array(stats["std"], dtype=np.float32)

    def is_ready(self) -> bool:
        return os.path.exists(MODEL_PATH)

    def predict(self, features: np.ndarray) -> Dict[str, float]:
        """推理：输入原始特征，自动标准化后预测"""
        if self.feature_mean is not None and self.feature_std is not None:
            features = (features - self.feature_mean) / self.feature_std
        return self.model.predict_value(features)

    def predict_match(
        self,
        spf_probs: Dict[str, float],
        rq_probs: Dict[str, float],
        score_top3: Dict[str, float],
        odds: Dict[str, float],
        elo_diff: float,
        odds_movement: Dict[str, float],
        competition: str,
    ) -> Dict[str, float]:
        """对单场比赛推理预测评分"""
        raw_feats = extract_features(
            spf_probs, rq_probs, score_top3, odds, elo_diff, odds_movement, competition
        )

        # 标准化
        if self.feature_mean is not None and self.feature_std is not None:
            feats = (raw_feats - self.feature_mean) / self.feature_std
        else:
            feats = raw_feats

        return self.model.predict_value(feats)

    def predict_from_db(self, match_id: int) -> Optional[Dict]:
        """从数据库加载比赛数据并推理"""
        from models import SessionLocal, Match, Prediction

        session = SessionLocal()
        try:
            match = session.query(Match).filter(Match.id == match_id).first()
            if not match:
                return None

            pred = session.query(Prediction).filter(
                Prediction.match_id == match_id,
                Prediction.play_type == "SPF",
            ).first()
            if not pred:
                return None

            spf = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)
            rq = spf  # fallback

            score_pred = session.query(Prediction).filter(
                Prediction.match_id == match_id,
                Prediction.play_type == "SCORE",
            ).first()
            score_probs = {}
            if score_pred and score_pred.probabilities:
                score_probs = score_pred.probabilities if isinstance(score_pred.probabilities, dict) else json.loads(score_pred.probabilities)

            odds = {
                "home": getattr(match, "closing_odds_home", None) or 2.0,
                "draw": getattr(match, "closing_odds_draw", None) or 3.0,
                "away": getattr(match, "closing_odds_away", None) or 2.0,
            }

            elo_diff = 0.0
            home_elo = getattr(match, "home_elo", None) or 0
            away_elo = getattr(match, "away_elo", None) or 0
            if home_elo and away_elo:
                elo_diff = home_elo - away_elo

            odds_movement = {}
            for sel in ["home", "draw", "away"]:
                closing = getattr(match, f"closing_odds_{sel}", None) or 0
                opening = getattr(match, f"opening_odds_{sel}", None) or 0
                if closing and opening:
                    odds_movement[sel] = (closing - opening) / opening
                else:
                    odds_movement[sel] = 0.0

            values = self.predict_match(
                spf_probs=spf,
                rq_probs=rq,
                score_top3=score_probs or spf,
                odds=odds,
                elo_diff=elo_diff,
                odds_movement=odds_movement,
                competition=match.competition or "",
            )

            # 生成预测建议
            best_sel = max(values, key=values.get)
            best_value = values[best_sel]
            sel_map = {"home": "主胜", "draw": "平", "away": "客胜"}

            return {
                "match_id": match_id,
                "match_code": match.match_code,
                "bet_values": values,
                "recommended": best_sel,
                "recommended_label": sel_map.get(best_sel, best_sel),
                "confidence": round(best_value, 3),
                "model_version": "bet_nn_v1",
                "ready": True,
            }
        finally:
            session.close()

    def get_training_status(self) -> Dict:
        """获取训练状态"""
        if not os.path.exists(TRAINING_LOG_PATH):
            return {"trained": False, "ready": False}

        try:
            with open(TRAINING_LOG_PATH, "r") as f:
                log = json.load(f)
            metrics = log.get("metrics", {})
            final_acc = metrics.get("val_accuracy", [0])[-1] if metrics.get("val_accuracy") else 0
            return {
                "trained": True,
                "ready": True,
                "trained_at": log.get("trained_at"),
                "final_val_accuracy": final_acc,
                "epochs": len(metrics.get("train_loss", [])),
            }
        except (json.JSONDecodeError, IOError):
            return {"trained": False, "ready": False}


# ────────────────────────────
# Scheduler 入口
# ────────────────────────────
def bet_nn_train_job() -> None:
    """每日训练定时任务"""
    trainer = BetNetTrainer()
    result = trainer.train()
    if result:
        logger.info(
            f"[bet-nn] Training done: epochs={result['epochs_trained']}, "
            f"val_acc={result['final_val_accuracy']:.1%}, "
            f"samples={result['samples']}"
        )
        if result["final_val_accuracy"] < 0.45:
            fire_alert("bet_nn", "warning",
                       f"预测网络验证准确率仅 {result['final_val_accuracy']:.1%}，需关注")
    else:
        logger.info("[bet-nn] Not enough training data, skipping")
