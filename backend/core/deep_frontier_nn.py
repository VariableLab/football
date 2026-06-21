"""
v4.0 Deep Frontier 深度学习时序 xG 物理融合引擎 (deep_frontier_nn.py)
"""
import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from utils.logger import get_logger

logger = get_logger("deep_frontier_nn")

MODEL_DIR = "./data/weights/nn"
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "deep_frontier.pt")
STATS_PATH = os.path.join(MODEL_DIR, "deep_frontier_stats.json")

SEQ_LEN = 5
SEQ_FEAT_DIM = 6
STATIC_FEAT_DIM = 48  # FeatureBuilder 基础特征维度


class TemporalEncoder(nn.Module):
    """
    时序状态 GRU 编码器：将 5 场走势特征编码为固定长度的状态 Embedding。
    """
    def __init__(self, feat_dim=SEQ_FEAT_DIM, hidden_dim=32):
        super().__init__()
        self.proj = nn.Linear(feat_dim, hidden_dim)
        self.gru = nn.GRU(hidden_dim, hidden_dim, batch_first=True)

    def forward(self, x):
        # x shape: (batch, seq_len, feat_dim)
        proj_x = F.relu(self.proj(x))
        _, h = self.gru(proj_x)  # h shape: (1, batch, hidden_dim)
        return h.squeeze(0)  # (batch, hidden_dim)


class xGTransformerNet(nn.Module):
    """
    深度期望进球 (xG) 残差回归模型。
    输入：主客队各自 5 场历史序列 + 48维博弈元特征。
    输出：主客队当前比赛期望进球 (lambda_home, lambda_away)。
    """
    def __init__(self, seq_feat_dim=SEQ_FEAT_DIM, static_feat_dim=STATIC_FEAT_DIM, hidden_dim=32):
        super().__init__()
        self.temp_encoder = TemporalEncoder(seq_feat_dim, hidden_dim)
        
        input_dim = 2 * hidden_dim + static_feat_dim
        self.input_bn = nn.BatchNorm1d(input_dim)
        
        # 残差 Block
        self.fc1 = nn.Linear(input_dim, 128)
        self.bn1 = nn.BatchNorm1d(128)
        self.fc2 = nn.Linear(128, 128)
        self.bn2 = nn.BatchNorm1d(128)
        
        # 双头输出 xG (lambda_home, lambda_away)
        self.head_home = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 1)
        )
        self.head_away = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 1)
        )
        self.relu = nn.ReLU()

    def forward(self, seq_h, seq_a, static_feats):
        # seq_h, seq_a: (batch, seq_len, seq_feat_dim)
        # static_feats: (batch, static_feat_dim)
        emb_h = self.temp_encoder(seq_h)
        emb_a = self.temp_encoder(seq_a)
        
        x = torch.cat([emb_h, emb_a, static_feats], dim=1)
        x = self.input_bn(x)
        
        identity = self.fc1(x)
        out = self.relu(self.bn1(identity))
        out = self.bn2(self.fc2(out))
        out += identity  # Skip connection
        out = self.relu(out)
        
        # Softplus 确保预测进球数严格大于 0。加入 0.1 偏置防止 Dixon-Coles 计算溢出
        lambda_h = F.softplus(self.head_home(out)) + 0.1
        lambda_a = F.softplus(self.head_away(out)) + 0.1
        
        return lambda_h, lambda_a


def build_match_history_vector(m, team_id) -> np.ndarray:
    """
    将单场历史赛果解析为 6 维状态向量。
    """
    actual_h = m.actual_home_goals if m.actual_home_goals is not None else 0
    actual_a = m.actual_away_goals if m.actual_away_goals is not None else 0
    
    if m.home_team_id == team_id:
        is_home = 1.0
        goals_scored = actual_h
        goals_conceded = actual_a
        opp_elo = m.away_team.elo if (m.away_team and m.away_team.elo is not None) else 1500
        odds = m.closing_odds_home or m.odds_home or 2.0
    else:
        is_home = 0.0
        goals_scored = actual_a
        goals_conceded = actual_h
        opp_elo = m.home_team.elo if (m.home_team and m.home_team.elo is not None) else 1500
        odds = m.closing_odds_away or m.odds_away or 2.0
        
    outcome = 1.0 if goals_scored > goals_conceded else (0.5 if goals_scored == goals_conceded else 0.0)
    
    return np.array([
        is_home,
        float(goals_scored) / 3.0,
        float(goals_conceded) / 3.0,
        float(opp_elo) / 1500.0,
        float(odds) / 5.0,
        outcome
    ], dtype=np.float32)


class DeepFrontierTrainer:
    """
    Deep Frontier (v4.0) 离线训练管理器。
    """
    def __init__(self, db_session=None):
        self.db = db_session
        self.model = xGTransformerNet()

    def get_team_seq(self, team_history: List, team_id, before_date) -> np.ndarray:
        """
        从历史列表里提取早于 before_date 的最近 5 场完赛数据，填充 0 向量。
        """
        b_date = before_date.replace(tzinfo=None) if before_date.tzinfo is not None else before_date
        
        valid = []
        for m in team_history:
            if m.kickoff_at is None:
                continue
            m_date = m.kickoff_at.replace(tzinfo=None) if m.kickoff_at.tzinfo is not None else m.kickoff_at
            if m_date < b_date:
                valid.append(m)
                
        recent = valid[-SEQ_LEN:]
        
        seq = []
        for m in recent:
            seq.append(build_match_history_vector(m, team_id))
            
        while len(seq) < SEQ_LEN:
            seq.insert(0, np.zeros(SEQ_FEAT_DIM, dtype=np.float32)) # Padding
            
        return np.stack(seq)

    def build_training_data(self) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """
        一键拉取并内存构建无数据泄漏的时序特征数据集。
        """
        from database.models import SessionLocal, Match, MatchStatus
        from core.prediction_engine import build_context_from_match
        from features.feature_builder import FeatureBuilder
        
        s = self.db or SessionLocal()
        try:
            builder = FeatureBuilder(use_interactions=False) # 仅用 48 维基础特征
            
            # 1. 查找全部 FINISHED 比赛，排序必须严格升序（防泄漏）
            finished_matches = s.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None),
                Match.actual_away_goals.isnot(None),
                Match.kickoff_at.isnot(None)
            ).order_by(Match.kickoff_at.asc()).all()
            
            if len(finished_matches) < 200:
                logger.warning("Not enough finished matches for training.")
                return None
                
            # 2. 内存构建时序链
            from collections import defaultdict
            team_history = defaultdict(list)
            
            # 先缓存各比赛所需特征，避免在循环中每次都查询
            from features import EloModel, PoissonModel, MarketModel
            from features.form_markov_model import FormMarkovModel
            from features.h2h_model import H2HModel
            
            fm = FormMarkovModel(s)
            hm = H2HModel(s)
            
            train_seq_h, train_seq_a, train_static, train_y_h, train_y_a = [], [], [], [], []
            
            for idx, m in enumerate(finished_matches):
                if m.home_team is None or m.away_team is None:
                    continue
                
                # 获取该比赛前，主客队在 team_history 里的记录
                seq_h = self.get_team_seq(team_history[m.home_team_id], m.home_team_id, m.kickoff_at)
                seq_a = self.get_team_seq(team_history[m.away_team_id], m.away_team_id, m.kickoff_at)
                
                # 计算静态 48 维特征
                ctx = build_context_from_match(m)
                if not ctx:
                    # 加入历史列表，供后面的比赛引用
                    team_history[m.home_team_id].append(m)
                    team_history[m.away_team_id].append(m)
                    continue
                    
                elo = EloModel.predict(ctx)
                poisson = PoissonModel.predict(ctx)
                market = MarketModel.predict(ctx)
                
                form_feats = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id)
                h2h_feats = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
                
                static_feats = builder.build(elo, poisson, 1.0, market, form_feats, h2h_feats, ctx)
                
                train_seq_h.append(seq_h)
                train_seq_a.append(seq_a)
                train_static.append(static_feats)
                train_y_h.append(float(m.actual_home_goals))
                train_y_a.append(float(m.actual_away_goals))
                
                # 将此场完赛比赛加入主客队的战绩历史，供后续赛程回溯引用
                team_history[m.home_team_id].append(m)
                team_history[m.away_team_id].append(m)
                
            return (
                np.stack(train_seq_h),
                np.stack(train_seq_a),
                np.stack(train_static),
                np.array(train_y_h, dtype=np.float32),
                np.array(train_y_a, dtype=np.float32)
            )
        finally:
            if not self.db:
                s.close()

    def train(self) -> Optional[Dict]:
        """
        开始时序前向验证训练。
        """
        data = self.build_training_data()
        if not data:
            return None
        seq_h, seq_a, static, y_h, y_a = data
        
        # 80/20 时序划分
        split = int(len(static) * 0.8)
        
        t_seq_h, t_seq_a, t_static, t_y_h, t_y_a = (
            torch.FloatTensor(seq_h[:split]),
            torch.FloatTensor(seq_a[:split]),
            torch.FloatTensor(static[:split]),
            torch.FloatTensor(y_h[:split]),
            torch.FloatTensor(y_a[:split])
        )
        
        v_seq_h, v_seq_a, v_static, v_y_h, v_y_a = (
            torch.FloatTensor(seq_h[split:]),
            torch.FloatTensor(seq_a[split:]),
            torch.FloatTensor(static[split:]),
            torch.FloatTensor(y_h[split:]),
            torch.FloatTensor(y_a[split:])
        )
        
        dataset = list(zip(t_seq_h, t_seq_a, t_static, t_y_h, t_y_a))
        loader = DataLoader(dataset, batch_size=64, shuffle=True)
        
        self.model = xGTransformerNet()
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=1e-3, weight_decay=1e-3)
        # Poisson 负对数似然损失：log_input=False 保证 y_pred 为 lambda 本身
        criterion = nn.PoissonNLLLoss(log_input=False)
        
        best_loss = float("inf")
        patience_counter = 0
        epochs = 80
        
        for ep in range(epochs):
            self.model.train()
            for b_sh, b_sa, b_st, b_yh, b_ya in loader:
                optimizer.zero_grad()
                pred_h, pred_a = self.model(b_sh, b_sa, b_st)
                loss_h = criterion(pred_h.squeeze(), b_yh)
                loss_a = criterion(pred_a.squeeze(), b_ya)
                loss = loss_h + loss_a
                loss.backward()
                optimizer.step()
                
            # 验证
            self.model.eval()
            with torch.no_grad():
                val_pred_h, val_pred_a = self.model(v_seq_h, v_seq_a, v_static)
                val_loss_h = criterion(val_pred_h.squeeze(), v_y_h)
                val_loss_a = criterion(val_pred_a.squeeze(), v_y_a)
                val_loss = (val_loss_h + val_loss_a).item()
                
            if val_loss < best_loss:
                best_loss = val_loss
                patience_counter = 0
                torch.save(self.model.state_dict(), MODEL_PATH)
            else:
                patience_counter += 1
                
            if ep % 5 == 0:
                logger.info(f"Epoch {ep}: Val Poisson NLL Loss = {val_loss:.4f}")
                
            if patience_counter >= 10:
                logger.info("Early stopping triggered.")
                break
                
        # 导出均值和标准差统计值用于推理归一化
        stats = {
            "mean": static.mean(axis=0).tolist(),
            "std": np.maximum(static.std(axis=0), 1e-2).tolist(),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "best_loss": best_loss
        }
        with open(STATS_PATH, "w") as f:
            json.dump(stats, f)
            
        logger.info(f"Training complete. Weights saved to {MODEL_PATH}")
        return stats


class DeepFrontierPredictor:
    """
    Deep Frontier (v4.0) 在线预测推理器。
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_session=None):
        if self._initialized:
            return
        self.db = db_session
        self.model = xGTransformerNet()
        self.stats = None
        self._load()
        self._initialized = True

    def _load(self):
        if os.path.exists(MODEL_PATH):
            try:
                self.model.load_state_dict(torch.load(MODEL_PATH, map_location=torch.device('cpu')))
                self.model.eval()
            except Exception as e:
                logger.error(f"Failed to load model weights: {e}")
        if os.path.exists(STATS_PATH):
            try:
                with open(STATS_PATH) as f:
                    self.stats = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load stats: {e}")

    def is_ready(self) -> bool:
        return self.stats is not None

    def get_team_seq_live(self, db, team_id, kickoff_at) -> np.ndarray:
        """
        实时从数据库查询某支球队之前的最近 5 场完赛比赛特征。
        """
        from database.models import Match, MatchStatus
        from sqlalchemy import or_
        
        matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            Match.kickoff_at < kickoff_at
        ).order_by(Match.kickoff_at.desc()).limit(SEQ_LEN).all()
        
        # 翻转使得时间变成升序
        matches.reverse()
        
        seq = []
        for m in matches:
            seq.append(build_match_history_vector(m, team_id))
            
        while len(seq) < SEQ_LEN:
            seq.insert(0, np.zeros(SEQ_FEAT_DIM, dtype=np.float32))
            
        return np.stack(seq)

    def predict_xg(self, db, ctx, static_features: np.ndarray) -> Optional[Tuple[float, float]]:
        """
        执行前向传播，推理得到 lambda_home (主期望进球) 和 lambda_away (客期望进球)。
        """
        if not self.is_ready():
            return None
            
        seq_h = self.get_team_seq_live(db, ctx.home_team.team_id, ctx.kickoff_at)
        seq_a = self.get_team_seq_live(db, ctx.away_team.team_id, ctx.kickoff_at)
        
        t_seq_h = torch.FloatTensor(seq_h).unsqueeze(0)
        t_seq_a = torch.FloatTensor(seq_a).unsqueeze(0)
        t_static = torch.FloatTensor(static_features).unsqueeze(0)
        
        with torch.no_grad():
            lam_h, lam_a = self.model(t_seq_h, t_seq_a, t_static)
            
        return float(lam_h.squeeze().item()), float(lam_a.squeeze().item())
