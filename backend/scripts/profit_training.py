import sys
import os
import json
import numpy as np
import torch
import torch.nn as nn
from datetime import datetime, timezone

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from residual_nn import ResidualNet, ResidualDataset, extract_residual_features, MODEL_DIR, RESIDUAL_MODEL_PATH
from logger import get_logger

logger = get_logger("profit_nn")

# --- 核心进化：ROI 损失函数 ---
class ROILoss(nn.Module):
    """
    利润导向损失函数。
    目标：不仅仅是预测对，而是最大化 (预测概率 * 赔率 - 1) 的正向期望。
    """
    def __init__(self):
        super().__init__()

    def forward(self, pred_delta, target_residual, odds):
        # 1. 基础回归损失 (确保概率不跑偏)
        mse = nn.functional.mse_loss(pred_delta, target_residual)
        
        # 2. 利润加权
        profit_weight = torch.clamp(odds - 1.0, 1.0, 5.0)
        weighted_mse = torch.mean(profit_weight * (pred_delta - target_residual)**2)
        
        return 0.7 * mse + 0.3 * weighted_mse

class ProfitTrainer:
    def __init__(self):
        self.model = ResidualNet()
        
    def train(self):
        print("Starting build_training_data...")
        from residual_nn import ResidualTrainer
        base_trainer = ResidualTrainer()
        data = base_trainer.build_training_data()
        if data is None: 
            print("No data returned from build_training_data")
            return
        
        X, R = data
        print(f"Data built: {len(X)} samples")
        
        odds_inv = X[:, 12:15]
        odds = 1.0 / np.maximum(odds_inv, 0.05)
        
        X_t = torch.FloatTensor(X)
        R_t = torch.FloatTensor(R)
        O_t = torch.FloatTensor(odds)
        
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=1e-3, weight_decay=1e-4)
        criterion = ROILoss()
        
        print(f"🚀 开始利润导向神经网络训练 (样本: {len(X)})...")
        
        for epoch in range(100):
            self.model.train()
            optimizer.zero_grad()
            
            output = self.model(X_t)
            loss = criterion(output, R_t, O_t)
            
            loss.backward()
            optimizer.step()
            
            if epoch % 20 == 0:
                print(f"  Epoch {epoch}: Loss = {loss.item():.6f}")
                
        # 保存模型
        torch.save(self.model.state_dict(), RESIDUAL_MODEL_PATH)
        print(f"✅ 利润导向模型已保存: {RESIDUAL_MODEL_PATH}")

if __name__ == "__main__":
    # Force print to stdout immediately
    sys.stdout.reconfigure(line_buffering=True)
    os.environ["SECRET_KEY"] = "temp-secret-key-at-least-32-characters-long"
    os.environ["ADMIN_API_KEY"] = "temp-admin-key-long-enough"
    trainer = ProfitTrainer()
    trainer.train()
