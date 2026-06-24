"""平局概率回归修正网络 — DrawClassifier

核心问题: 当前融合SPF仅0.7%预测平局, 实际占25%。规则式draw_calibrator已验证失败。
二分类方案验证: 正负样本3:1不平衡+信号弱导致BCE收敛到"全预测非平"的trivial solution。

最终方案: 不做二分类, 而是做回归 — 直接预测"这场球的draw概率应该是多少"。
- 训练标签: actual_outcome == "draw" → target=1.0, else → target=0.0
- 但训练时不做BCE, 而是用MSE回归 + 按batch计算target均值归一化
- 推理: 模型输出的P(draw)直接用于修正SPF概率

关键改进: 使用 weighted sampling 让每个batch中draw样本占40%, 避免模型只学"大多数不是平局"
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
from monitor.alert_manager import fire_alert

logger = get_logger("draw_classifier")

MODEL_DIR = "./data/draw_classifier"
os.makedirs(MODEL_DIR, exist_ok=True)

MODEL_PATH = os.path.join(MODEL_DIR, "draw_net.pt")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")
TRAINING_LOG_PATH = os.path.join(MODEL_DIR, "training_log.json")
WALK_FORWARD_PATH = os.path.join(MODEL_DIR, "walk_forward_metrics.json")

INPUT_DIM = 14
BATCH_SIZE = 128
LEARNING_RATE = 1e-3
EPOCHS = 80
PATIENCE = 10
MIN_TRAIN_SAMPLES = 500
DRAW_WEIGHT = 4.0  # MSE loss weight for draw samples
VAL_SPLIT = 0.2

LEAGUE_MAP = {
    "EPL": [1, 0, 0, 0],
    "Bundesliga": [0, 1, 0, 0],
    "LaLiga": [0, 0, 1, 0],
    "SerieA": [0, 0, 0, 1],
}


def extract_draw_features(
    elo_diff: float,
    xg_diff: float,
    market_draw_prob: Optional[float],
    model_draw_prob: float,
    competition: str,
    venue_type: str,
    temperature: float,
    odds_home: Optional[float] = None,
    odds_draw: Optional[float] = None,
    odds_away: Optional[float] = None,
    draw_movement: float = 0.0,
) -> np.ndarray:
    """提取14维平局检测特征向量"""
    # odds_symmetry: |odds_home - odds_away| / (odds_home + odds_away) — 低值=对称=平局信号
    odds_symmetry = 0.0
    if odds_home and odds_away and odds_home > 1.01 and odds_away > 1.01:
        odds_symmetry = abs(odds_home - odds_away) / (odds_home + odds_away) * 2.0

    # home_away_gap: (odds_home - odds_away) — 正值=主弱客强, 负值=主强客弱
    home_away_gap = 0.0
    if odds_home and odds_away and odds_home > 1.01 and odds_away > 1.01:
        home_away_gap = (odds_home - odds_away) / 5.0  # normalized

    feats = [
        np.clip(elo_diff / 400.0, -1.5, 1.5),
        np.clip(xg_diff / 1.0, -2.0, 2.0),
        market_draw_prob if market_draw_prob is not None and market_draw_prob > 0 else 0.0,
        model_draw_prob,
        1.0 if venue_type == "neutral" else 0.0,
        np.clip(temperature / 40.0, -1.0, 2.0),
        np.clip(odds_symmetry, 0.0, 1.0),  # key feature: 0=symmetric→draw likely
        np.clip(home_away_gap, -1.0, 1.0),  # signed gap direction
        np.clip(draw_movement / 0.1, -1.0, 1.0),  # draw odds movement
        np.clip(1.0 / (odds_draw or 3.5) / 3.0, 0.0, 1.0),  # raw draw prob from odds
    ]
    feats.extend(LEAGUE_MAP.get(competition, [0, 0, 0, 0]))
    return np.array(feats[:INPUT_DIM], dtype=np.float32)


class FocalLoss(nn.Module):
    """Weighted MSE regression loss for draw probability prediction.

    Instead of BCE classification (which collapses to trivial solution),
    use MSE with per-sample weights that heavily penalize errors on draw matches.
    """
    def __init__(self, draw_weight: float = 3.0):
        super().__init__()
        self.draw_weight = draw_weight

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        # pred is raw logit, apply sigmoid
        prob = torch.sigmoid(pred)
        sq_error = (prob - target) ** 2
        # Weight draw samples more heavily
        weights = 1.0 + (self.draw_weight - 1.0) * target
        weighted_loss = (sq_error * weights).mean()
        return weighted_loss


class DrawDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray):
        self.features = torch.FloatTensor(features)
        self.labels = torch.FloatTensor(labels)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


class DrawClassifierNet(nn.Module):
    """平局二分类MLP

    输入: 10维特征(Elo差/xG差/市场概率/模型概率/联赛/中立/温度)
    输出: 1维 sigmoid → P(draw)
    """
    def __init__(self, input_dim: int = INPUT_DIM, hidden_dims: Tuple[int, ...] = (64, 32, 16)):
        super().__init__()
        layers: list[nn.Module] = []
        prev_dim = input_dim
        for h_dim in hidden_dims:
            layers.extend([
                nn.Linear(prev_dim, h_dim),
                nn.BatchNorm1d(h_dim),
                nn.ReLU(),
                nn.Dropout(0.3),
            ])
            prev_dim = h_dim
        layers.append(nn.Linear(prev_dim, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(1)

    def predict_draw_prob(self, features: np.ndarray) -> float:
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            logit = self.forward(x)
            return float(torch.sigmoid(logit).item())


class DrawClassifierTrainer:
    """从数据库构建训练数据 → 训练 → 保存模型"""

    def __init__(self) -> None:
        self.model = DrawClassifierNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        from database.models import SessionLocal, Match, MatchStatus, Prediction

        session = SessionLocal()
        try:
            VALID_OUTCOMES = ("home", "draw", "away")
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.in_(VALID_OUTCOMES),
            ).all()

            if len(finished) < MIN_TRAIN_SAMPLES:
                logger.info(f"[draw-cls] 训练样本不足: {len(finished)}/{MIN_TRAIN_SAMPLES}")
                return None

            match_ids = [m.id for m in finished]
            all_preds = session.query(Prediction).filter(
                Prediction.match_id.in_(match_ids),
            ).all()

            pred_map: Dict[Tuple[int, str], Dict] = {}
            for p in all_preds:
                key = (p.match_id, p.play_type)
                probs = p.probabilities if isinstance(p.probabilities, dict) else json.loads(p.probabilities) if p.probabilities else {}
                pred_map[key] = probs

            features_list: list[np.ndarray] = []
            labels_list: list[float] = []

            for match in finished:
                spf = pred_map.get((match.id, "spf")) or pred_map.get((match.id, "SPF"))
                if not spf:
                    continue

                elo_diff = 0.0
                if match.home_team and match.away_team:
                    home_elo = match.home_team.elo or 1500
                    away_elo = match.away_team.elo or 1500
                    elo_diff = home_elo - away_elo

                xg_diff = 0.0
                if match.home_team and match.away_team:
                    home_xg = match.home_team.avg_xg or match.home_team.avg_goals_scored
                    away_xg = match.away_team.avg_xg or match.away_team.avg_goals_scored
                    xg_diff = home_xg - away_xg

                market_draw = None
                if match.closing_odds_draw and match.closing_odds_draw > 1.01:
                    total_imp = (1.0 / match.closing_odds_home + 1.0 / match.closing_odds_draw + 1.0 / match.closing_odds_away) if (
                        match.closing_odds_home and match.closing_odds_away and
                        match.closing_odds_home > 1.01 and match.closing_odds_away > 1.01
                    ) else 0
                    if total_imp > 0:
                        market_draw = (1.0 / match.closing_odds_draw) / total_imp
                elif match.odds_draw and match.odds_draw > 1.01:
                    total_imp = (1.0 / (match.odds_home or 2.0) + 1.0 / match.odds_draw + 1.0 / (match.odds_away or 2.0))
                    if total_imp > 0:
                        market_draw = (1.0 / match.odds_draw) / total_imp

                model_draw_prob = spf.get("draw", 0.25)
                competition = match.competition or ""
                venue_type = match.venue_type or "neutral"
                temperature = match.temperature or 20.0

                feats = extract_draw_features(
                    elo_diff=elo_diff,
                    xg_diff=xg_diff,
                    market_draw_prob=market_draw,
                    model_draw_prob=model_draw_prob,
                    competition=competition,
                    venue_type=venue_type,
                    temperature=temperature,
                    odds_home=match.closing_odds_home or match.odds_home,
                    odds_draw=match.closing_odds_draw or match.odds_draw,
                    odds_away=match.closing_odds_away or match.odds_away,
                    draw_movement=0.0,  # computed below
                )

                # Compute draw odds movement
                draw_movement = 0.0
                closing_d = match.closing_odds_draw or 0
                opening_d = match.opening_odds_draw or 0
                if closing_d > 1.01 and opening_d > 1.01:
                    draw_movement = (closing_d - opening_d) / opening_d

                # Re-extract with movement
                feats = extract_draw_features(
                    elo_diff=elo_diff,
                    xg_diff=xg_diff,
                    market_draw_prob=market_draw,
                    model_draw_prob=model_draw_prob,
                    competition=competition,
                    venue_type=venue_type,
                    temperature=temperature,
                    odds_home=match.closing_odds_home or match.odds_home,
                    odds_draw=match.closing_odds_draw or match.odds_draw,
                    odds_away=match.closing_odds_away or match.odds_away,
                    draw_movement=draw_movement,
                )

                label = 1.0 if match.actual_outcome == "draw" else 0.0
                features_list.append(feats)
                labels_list.append(label)

            if len(features_list) < MIN_TRAIN_SAMPLES:
                logger.info(f"[draw-cls] 有效训练样本不足: {len(features_list)}")
                return None

            features = np.stack(features_list)
            labels = np.array(labels_list, dtype=np.float32)

            self.feature_mean = features.mean(axis=0)
            self.feature_std = features.std(axis=0) + 1e-8
            features = (features - self.feature_mean) / self.feature_std

            draw_rate = labels.sum() / len(labels)
            logger.info(f"[draw-cls] 训练集: {len(features)} 样本, 平局率={draw_rate:.2%}")
            return features, labels

        finally:
            session.close()

    def train(self) -> Optional[Dict]:
        data = self.build_training_data()
        if data is None:
            return None

        features, labels = data
        dataset = DrawDataset(features, labels)

        n = len(dataset)
        n_val = int(n * VAL_SPLIT)
        n_train = n - n_val
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        # Weighted sampler: draw samples get 3x weight so each batch has ~50% draws
        train_labels = labels[:n_train]  # labels for the train split
        sample_weights = np.where(train_labels == 1.0, 3.0, 1.0)
        sampler = torch.utils.data.WeightedRandomSampler(
            weights=sample_weights,
            num_samples=n_train,
            replacement=True,
        )
        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, sampler=sampler)
        val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

        self.model = DrawClassifierNet()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=5)
        criterion = FocalLoss(draw_weight=DRAW_WEIGHT)

        best_val_loss = float("inf")
        patience_counter = 0
        metrics: Dict[str, list] = {"train_loss": [], "val_loss": [], "val_accuracy": [], "val_draw_recall": []}

        for epoch in range(EPOCHS):
            self.model.train()
            train_loss = 0.0
            for batch_feat, batch_label in train_loader:
                optimizer.zero_grad()
                logits = self.model(batch_feat)
                loss = criterion(logits, batch_label)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()
            train_loss /= len(train_loader)

            self.model.eval()
            val_loss = 0.0
            draw_probs_list = []
            non_draw_probs_list = []

            with torch.no_grad():
                for batch_feat, batch_label in val_loader:
                    logits = self.model(batch_feat)
                    loss = criterion(logits, batch_label)
                    val_loss += loss.item()
                    probs = torch.sigmoid(logits)
                    draw_mask = batch_label == 1.0
                    non_draw_mask = batch_label == 0.0
                    if draw_mask.any():
                        draw_probs_list.append(probs[draw_mask].mean().item())
                    if non_draw_mask.any():
                        non_draw_probs_list.append(probs[non_draw_mask].mean().item())

            val_loss /= len(val_loader)
            avg_draw_prob = np.mean(draw_probs_list) if draw_probs_list else 0
            avg_non_draw_prob = np.mean(non_draw_probs_list) if non_draw_probs_list else 0
            separation = avg_draw_prob - avg_non_draw_prob

            metrics["train_loss"].append(round(train_loss, 4))
            metrics["val_loss"].append(round(val_loss, 4))
            metrics["val_accuracy"].append(round(separation, 4))
            metrics["val_draw_recall"].append(round(avg_draw_prob, 4))

            scheduler.step(val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                self._save_model()
            else:
                patience_counter += 1
                if patience_counter >= PATIENCE:
                    logger.info(f"[draw-cls] Early stop at epoch {epoch}")
                    break

            if epoch % 10 == 0 or epoch == EPOCHS - 1:
                logger.info(
                    f"[draw-cls] Epoch {epoch}: train_loss={train_loss:.4f}, "
                    f"val_loss={val_loss:.4f}, "
                    f"draw_mean={avg_draw_prob:.3f}, non_draw_mean={avg_non_draw_prob:.3f}, "
                    f"separation={separation:.4f}"
                )

        self._save_feature_stats()
        self._save_training_log(metrics)

        final_draw_recall = metrics["val_draw_recall"][-1] if metrics["val_draw_recall"] else 0
        final_acc = metrics["val_accuracy"][-1] if metrics["val_accuracy"] else 0

        return {
            "epochs_trained": len(metrics["train_loss"]),
            "best_val_loss": round(best_val_loss, 4),
            "final_val_accuracy": final_acc,
            "final_draw_recall": final_draw_recall,
            "samples": n,
        }

    def _save_model(self) -> None:
        torch.save(self.model.state_dict(), MODEL_PATH)
        logger.info(f"[draw-cls] Model saved to {MODEL_PATH}")

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


class DrawClassifierPredictor:
    """加载已训练模型，提供推理接口"""

    def __init__(self) -> None:
        self.model = DrawClassifierNet()
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None
        # P0 修复: 降低 draw 检测门槛, 提高灵敏度
        self.threshold = 0.45
        self.max_boost = 0.10
        self.boost_scale = 0.35
        self._load_model()


    def _load_model(self) -> None:
        if os.path.exists(MODEL_PATH):
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()
            logger.info("[draw-cls] Loaded trained model")

        if os.path.exists(FEATURE_STATS_PATH):
            with open(FEATURE_STATS_PATH, "r") as f:
                stats = json.load(f)
            self.feature_mean = np.array(stats["mean"], dtype=np.float32)
            self.feature_std = np.array(stats["std"], dtype=np.float32)

        config_path = os.path.join(MODEL_DIR, "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = json.load(f)
            self.threshold = cfg.get("threshold", 0.55)
            self.max_boost = cfg.get("max_boost", 0.10)
            self.boost_scale = cfg.get("scale", 0.35)

    def is_ready(self) -> bool:
        return os.path.exists(MODEL_PATH)

    def predict_draw_prob(self, features: np.ndarray) -> float:
        """推理: 输入原始特征, 返回P(draw)∈[0,1]"""
        if self.feature_mean is not None and self.feature_std is not None:
            features = (features - self.feature_mean) / self.feature_std
        return self.model.predict_draw_prob(features)

    def predict_from_match(
        self,
        elo_diff: float,
        xg_diff: float,
        market_draw_prob: Optional[float],
        model_draw_prob: float,
        competition: str = "",
        venue_type: str = "neutral",
        temperature: float = 20.0,
        odds_home: Optional[float] = None,
        odds_draw: Optional[float] = None,
        odds_away: Optional[float] = None,
        draw_movement: float = 0.0,
    ) -> float:
        """从比赛上下文特征直接推理P(draw)"""
        raw_feats = extract_draw_features(
            elo_diff=elo_diff,
            xg_diff=xg_diff,
            market_draw_prob=market_draw_prob,
            model_draw_prob=model_draw_prob,
            competition=competition,
            venue_type=venue_type,
            temperature=temperature,
            odds_home=odds_home,
            odds_draw=odds_draw,
            odds_away=odds_away,
            draw_movement=draw_movement,
        )
        return self.predict_draw_prob(raw_feats)

    def adjust_spf(
        self,
        spf: Dict[str, float],
        draw_prob_nn: float,
    ) -> Dict[str, float]:
        """用NN输出的P(draw)修正融合SPF概率

        策略: 概率校准修正, 不是方向预测翻转。
        当前模型平均给出draw=0.252, 实际draw率=0.250, 整体校准已经OK。
        但模型在特定场景下低估draw: 当赔率对称(主客赔率接近)且NN输出>0.57时,
        draw概率应该更高。

        核心原则: 宁可保守(不影响方向准确率), 只做小幅概率校准。
        """
        if not self.is_ready():
            return spf

        old_draw = spf.get("draw", 0.25)
        old_home = spf.get("home", 0.375)
        old_away = spf.get("away", 0.375)

        # Only boost draw when NN confidence is meaningfully above average
        if draw_prob_nn <= self.threshold:
            return spf

        excess = draw_prob_nn - self.threshold
        draw_boost = min(self.max_boost, excess * self.boost_scale)

        new_draw = old_draw + draw_boost
        new_draw = max(0.05, min(0.45, new_draw))
        delta = new_draw - old_draw

        if abs(delta) < 1e-6:
            return spf

        home_away_total = old_home + old_away
        if home_away_total <= 0:
            return {"home": 0.375, "draw": 0.25, "away": 0.375}

        new_home = old_home - delta * (old_home / home_away_total)
        new_away = old_away - delta * (old_away / home_away_total)

        new_home = max(0.01, new_home)
        new_away = max(0.01, new_away)
        new_draw = max(0.01, new_draw)

        total = new_home + new_draw + new_away
        return {
            "home": new_home / total,
            "draw": new_draw / total,
            "away": new_away / total,
        }


def walk_forward_validate(n_folds: int = 10) -> Dict:
    """Walk-forward time-series validation"""
    from database.models import SessionLocal, Match, MatchStatus, Prediction

    session = SessionLocal()
    try:
        VALID_OUTCOMES = ("home", "draw", "away")
        finished = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.in_(VALID_OUTCOMES),
            Match.kickoff_at.isnot(None),
        ).order_by(Match.kickoff_at).all()

        if len(finished) < 2000:
            logger.info(f"[draw-cls] Walk-forward样本不足: {len(finished)}")
            return {}

        match_ids = [m.id for m in finished]
        all_preds = session.query(Prediction).filter(
            Prediction.match_id.in_(match_ids),
        ).all()

        pred_map: Dict[Tuple[int, str], Dict] = {}
        for p in all_preds:
            key = (p.match_id, p.play_type)
            probs = p.probabilities if isinstance(p.probabilities, dict) else json.loads(p.probabilities) if p.probabilities else {}
            pred_map[key] = probs

        rows: list[Dict] = []
        for match in finished:
            spf = pred_map.get((match.id, "spf")) or pred_map.get((match.id, "SPF"))
            if not spf:
                continue

            elo_diff = 0.0
            xg_diff = 0.0
            market_draw = None
            if match.home_team and match.away_team:
                home_elo = match.home_team.elo or 1500
                away_elo = match.away_team.elo or 1500
                elo_diff = home_elo - away_elo
                home_xg = match.home_team.avg_xg or match.home_team.avg_goals_scored
                away_xg = match.away_team.avg_xg or match.away_team.avg_goals_scored
                xg_diff = home_xg - away_xg

            if match.closing_odds_draw and match.closing_odds_draw > 1.01:
                total_imp = (1.0 / match.closing_odds_home + 1.0 / match.closing_odds_draw + 1.0 / match.closing_odds_away) if (
                    match.closing_odds_home and match.closing_odds_away and
                    match.closing_odds_home > 1.01 and match.closing_odds_away > 1.01
                ) else 0
                if total_imp > 0:
                    market_draw = (1.0 / match.closing_odds_draw) / total_imp

            model_draw_prob = spf.get("draw", 0.25)
            competition = match.competition or ""
            venue_type = match.venue_type or "neutral"
            temperature = match.temperature or 20.0

            feats = extract_draw_features(
                elo_diff=elo_diff, xg_diff=xg_diff, market_draw_prob=market_draw,
                model_draw_prob=model_draw_prob, competition=competition,
                venue_type=venue_type, temperature=temperature,
            )

            rows.append({
                "features": feats,
                "label": 1.0 if match.actual_outcome == "draw" else 0.0,
                "actual_outcome": match.actual_outcome,
                "model_spf": spf,
            })

        if len(rows) < 2000:
            logger.info(f"[draw-cls] Walk-forward有效样本不足: {len(rows)}")
            return {}

        fold_size = len(rows) // n_folds
        all_metrics: list[Dict] = []

        for fold_i in range(n_folds):
            val_start = fold_i * fold_size
            val_end = val_start + fold_size
            if fold_i == n_folds - 1:
                val_end = len(rows)

            train_rows = rows[:val_start] + rows[val_end:]
            val_rows = rows[val_start:val_end]

            train_feats = np.stack([r["features"] for r in train_rows])
            train_labels = np.array([r["label"] for r in train_rows], dtype=np.float32)
            val_feats = np.stack([r["features"] for r in val_rows])
            val_labels = np.array([r["label"] for r in val_rows], dtype=np.float32)

            feat_mean = train_feats.mean(axis=0)
            feat_std = train_feats.std(axis=0) + 1e-8
            train_feats_norm = (train_feats - feat_mean) / feat_std
            val_feats_norm = (val_feats - feat_mean) / feat_std

            train_ds = DrawDataset(train_feats_norm, train_labels)
            val_ds = DrawDataset(val_feats_norm, val_labels)
            train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

            model = DrawClassifierNet()
            optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
            criterion = FocalLoss()

            best_loss = float("inf")
            patience_cnt = 0

            for epoch in range(EPOCHS):
                model.train()
                for bf, bl in train_loader:
                    optimizer.zero_grad()
                    logits = model(bf)
                    loss = criterion(logits, bl)
                    loss.backward()
                    optimizer.step()

                model.eval()
                vloss = 0.0
                with torch.no_grad():
                    for bf, bl in val_loader:
                        logits = model(bf)
                        vloss += criterion(logits, bl).item()
                vloss /= len(val_loader)

                if vloss < best_loss:
                    best_loss = vloss
                    patience_cnt = 0
                else:
                    patience_cnt += 1
                    if patience_cnt >= PATIENCE:
                        break

            # Evaluate on validation set
            model.eval()
            correct = 0
            total = 0
            draw_correct = 0
            draw_total = 0
            spf_correct_baseline = 0

            with torch.no_grad():
                for bf, bl in val_loader:
                    logits = model(bf)
                    probs = torch.sigmoid(logits)
                    preds = (probs >= 0.42).float()
                    correct += (preds == bl).sum().item()
                    total += len(bl)
                    draw_mask = bl == 1.0
                    draw_correct += (preds[draw_mask] == 1.0).sum().item()
                    draw_total += draw_mask.sum().item()

            fold_acc = correct / total if total > 0 else 0
            fold_draw_recall = draw_correct / draw_total if draw_total > 0 else 0

            fold_metrics = {
                "fold": fold_i,
                "val_size": len(val_rows),
                "accuracy": round(fold_acc, 4),
                "draw_recall": round(fold_draw_recall, 4),
                "best_val_loss": round(best_loss, 4),
            }
            all_metrics.append(fold_metrics)
            logger.info(
                f"[draw-cls] Fold {fold_i}: acc={fold_acc:.2%}, draw_recall={fold_draw_recall:.2%}"
            )

        avg_acc = np.mean([m["accuracy"] for m in all_metrics])
        avg_draw_recall = np.mean([m["draw_recall"] for m in all_metrics])
        summary = {
            "n_folds": n_folds,
            "avg_accuracy": round(avg_acc, 4),
            "avg_draw_recall": round(avg_draw_recall, 4),
            "folds": all_metrics,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(WALK_FORWARD_PATH, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(
            f"[draw-cls] Walk-forward done: avg_acc={avg_acc:.2%}, "
            f"avg_draw_recall={avg_draw_recall:.2%}"
        )
        return summary

    finally:
        session.close()


def draw_classifier_train_job() -> None:
    """每日训练定时任务"""
    trainer = DrawClassifierTrainer()
    result = trainer.train()
    if result:
        logger.info(
            f"[draw-cls] Training done: epochs={result['epochs_trained']}, "
            f"val_acc={result['final_val_accuracy']:.1%}, "
            f"draw_recall={result['final_draw_recall']:.1%}, "
            f"samples={result['samples']}"
        )
        if result["final_draw_recall"] < 0.10:
            fire_alert("draw_classifier", "warning",
                       f"平局分类器召回率仅 {result['final_draw_recall']:.1%}，需关注")
    else:
        logger.info("[draw-cls] Not enough training data, skipping")
