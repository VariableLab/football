"""
Residual Bet Neural Network — 残差修正网络 (v3)
集成 Andrej Karpathy 的工程思想：第一性原理、零冗余、高精度。

架构：
  - 输入：48 维特征向量 (from FeatureBuilder) + LR 概率输出 (3 维) + 市场赔率 (3 维) = 54 维
  - 目标：实际赛果 (one-hot)
  - 模式：从“残差回归”升级为“Stacking 分类”，直接学习融合后的置信度。
"""
import json, os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torch.optim.lr_scheduler import OneCycleLR
from logger import get_logger

logger = get_logger("residual_nn")

# 配置
MODEL_DIR = "./data/weights/nn"
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "stacking_v3.pt")
STATS_PATH = os.path.join(MODEL_DIR, "stacking_stats.json")

INPUT_DIM = 54  # 48 (FeatureBuilder) + 3 (LR) + 3 (Market)
OUTPUT_DIM = 3
BATCH_SIZE = 128
LEARNING_RATE = 3e-4
EPOCHS = 100
PATIENCE = 12


class StackingNet(nn.Module):
    """
    高精度叠加集成网络。
    使用深层 MLP + 残差连接 (Skip Connections) + 强 Regularization。
    """
    def __init__(self, input_dim=INPUT_DIM):
        super().__init__()
        self.input_bn = nn.BatchNorm1d(input_dim)
        
        # 骨干网络：ResNet 风格
        self.fc1 = nn.Linear(input_dim, 128)
        self.bn1 = nn.BatchNorm1d(128)
        self.fc2 = nn.Linear(128, 128)
        self.bn2 = nn.BatchNorm1d(128)
        
        self.head = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.4),
            nn.Linear(64, OUTPUT_DIM)
        )
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.input_bn(x)
        
        # Block 1
        identity = self.fc1(x)
        out = self.relu(self.bn1(identity))
        out = self.bn2(self.fc2(out))
        out += identity # 残差连接
        out = self.relu(out)
        
        return self.head(out)


class StackingTrainer:
    def __init__(self, db_session=None):
        self.db = db_session
        self.model = StackingNet()

    def build_training_data(self):
        """
        从数据库构建全量特征训练集。
        """
        from database.models import Match, MatchStatus, Prediction, PlayType
        from core.prediction_engine import build_context_from_match
        from features.feature_builder import FeatureBuilder
        
        s = self.db or SessionLocal()
        try:
            builder = FeatureBuilder(use_interactions=True)
            
            # 1. 查历史已完成比赛 (带赔率)
            finished = s.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.closing_odds_home.isnot(None)
            ).order_by(Match.kickoff_at.desc()).limit(5000).all()
            
            if len(finished) < 100: return None
            
            X, Y = [], []
            for m in finished:
                try:
                    ctx = build_context_from_match(m)
                    # 这里的特征构建要与 PredictionEngine 保持 100% 对齐
                    # 简化：仅使用 Elo, Poisson, Market 基准，暂不依赖 DB 查询以免过慢
                    from features import EloModel, PoissonModel, MarketModel
                    elo = EloModel.predict(ctx)
                    poisson = PoissonModel.predict(ctx)
                    market = MarketModel.predict(ctx)
                    
                    base_feats = builder.build(elo, poisson, 1.0, market, None, None, ctx)
                    
                    # 组合最终输入：[48维特征] + [Market 3维] + [Dummy placeholder for LR 3维]
                    # 注意：训练时如果没有 LR 记录，可以用 Market 占位
                    lr_dummy = np.array([market.get('home', 0.33), market.get('draw', 0.33), market.get('away', 0.33)], dtype=np.float32)
                    mkt_probs = np.array([market.get('home', 0.33), market.get('draw', 0.33), market.get('away', 0.33)], dtype=np.float32)
                    
                    full_vec = np.concatenate([base_feats, lr_dummy, mkt_probs])
                    
                    o2i = {"home":0, "draw":1, "away":2}
                    label = o2i.get(m.actual_outcome)
                    if label is not None:
                        X.append(full_vec)
                        Y.append(label)
                except: continue
                
            return np.stack(X), np.array(Y)
        finally:
            if not self.db: s.close()

    def train(self):
        data = self.build_training_data()
        if data is None: return None
        X, Y = data
        
        # 分离验证集
        idx = np.random.permutation(len(X))
        split = int(len(X) * 0.8)
        tr_idx, va_idx = idx[:split], idx[split:]
        
        tr_x, tr_y = torch.FloatTensor(X[tr_idx]), torch.LongTensor(Y[tr_idx])
        va_x, va_y = torch.FloatTensor(X[va_idx]), torch.LongTensor(Y[va_idx])
        
        tl = DataLoader(list(zip(tr_x, tr_y)), batch_size=BATCH_SIZE, shuffle=True)
        vl = DataLoader(list(zip(va_x, va_y)), batch_size=BATCH_SIZE)
        
        self.model = StackingNet()
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=LEARNING_RATE, weight_decay=0.01)
        criterion = nn.CrossEntropyLoss()
        scheduler = OneCycleLR(optimizer, max_lr=1e-3, steps_per_epoch=len(tl), epochs=EPOCHS)
        
        best_loss = float("inf")
        early_stop = 0
        
        for ep in range(EPOCHS):
            self.model.train()
            for bx, by in tl:
                optimizer.zero_grad()
                pred = self.model(bx)
                loss = criterion(pred, by)
                loss.backward()
                optimizer.step()
                scheduler.step()
            
            # 验证
            self.model.eval()
            val_loss = 0
            correct = 0
            with torch.no_grad():
                for bx, by in vl:
                    pred = self.model(bx)
                    val_loss += criterion(pred, by).item()
                    correct += (pred.argmax(1) == by).sum().item()
            
            val_loss /= len(vl)
            acc = correct / len(va_y)
            
            if val_loss < best_loss:
                best_loss = val_loss
                early_stop = 0
                torch.save(self.model.state_dict(), MODEL_PATH)
            else:
                early_stop += 1
            
            if ep % 5 == 0:
                logger.info(f"Epoch {ep}: Val Loss={val_loss:.4f}, Acc={acc:.1%}")
                
            if early_stop >= PATIENCE:
                logger.info("Early stopping triggered.")
                break
        
        # 保存统计信息供推理归一化
        stats = {
            "mean": X.mean(axis=0).tolist(),
            "std": np.maximum(X.std(axis=0), 1e-6).tolist(),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "best_loss": best_loss
        }
        with open(STATS_PATH, "w") as f: json.dump(stats, f)
        return stats


class StackingPredictor:
    def __init__(self):
        self.model = StackingNet()
        self.stats = None
        self._load()

    def _load(self):
        if os.path.exists(MODEL_PATH):
            self.model.load_state_dict(torch.load(MODEL_PATH))
            self.model.eval()
        if os.path.exists(STATS_PATH):
            with open(STATS_PATH) as f: self.stats = json.load(f)

    def is_ready(self): return self.stats is not None

    def predict(self, features: np.ndarray) -> Dict[str, float]:
        if not self.is_ready(): return None
        
        # 归一化
        mean = np.array(self.stats["mean"], dtype=np.float32)
        std = np.array(self.stats["std"], dtype=np.float32)
        x = (features - mean) / std
        
        with torch.no_grad():
            logits = self.model(torch.FloatTensor(x).unsqueeze(0))
            probs = torch.softmax(logits, dim=1).squeeze().numpy()
            
        return {"home": float(probs[0]), "draw": float(probs[1]), "away": float(probs[2])}
