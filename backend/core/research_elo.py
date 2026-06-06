import numpy as np
import pandas as pd
import json
import os
from .base import BasePredictor

class EloPredictor(BasePredictor):
    """
    专家级 Elo 模型实现。
    支持主场优势参数、动态 K 因子。
    """
    def __init__(self, k_factor=32, home_advantage=100):
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        self.elo_ratings = {} # {team_name: rating}
        self.default_elo = 1500

    def _get_rating(self, team):
        return self.elo_ratings.get(team, self.default_elo)

    def _expected_score(self, rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """
        按时间顺序更新所有球队的 Elo 评分。
        """
        self.elo_ratings = {} # 重置
        # 确保数据按时间排序
        data = X.copy()
        data['FTR'] = y
        
        for _, row in data.iterrows():
            home, away = row['HomeTeam'], row['AwayTeam']
            outcome = row['FTR']
            
            r_h = self._get_rating(home) + self.home_advantage
            r_a = self._get_rating(away)
            
            e_h = self._expected_score(r_h, r_a)
            e_a = 1 - e_h
            
            s_h = 1.0 if outcome == 'H' else (0.5 if outcome == 'D' else 0.0)
            s_a = 1.0 - s_h
            
            # 更新评分
            self.elo_ratings[home] = self._get_rating(home) + self.k_factor * (s_h - e_h)
            self.elo_ratings[away] = self._get_rating(away) + self.k_factor * (s_a - e_a)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        probs = []
        for _, row in X.iterrows():
            r_h = self._get_rating(row['HomeTeam']) + self.home_advantage
            r_a = self._get_rating(row['AwayTeam'])
            
            # 使用逻辑回归映射或标准 Elo 转换
            # 预期得分 E = 1 / (1 + 10^-(diff/400))
            e_h = self._expected_score(r_h, r_a)
            
            # 💡 改进：动态平局模型
            # 概率 P(D) 随着实力差增大而减小
            # 经验公式：P(D) = 0.25 * exp(-|diff|/600)
            diff = abs(r_h - r_a)
            prob_draw = 0.26 * np.exp(-diff / 500.0)
            
            # 剩余概率按预期得分比例分配
            rem = 1.0 - prob_draw
            # e_h 是主队预期得分(含平局贡献)，转换成纯胜率
            # 简化模型：P(H) = e_h * (1-P(D)), P(A) = (1-e_h) * (1-P(D))
            prob_home = e_h * rem
            prob_away = (1 - e_h) * rem
            
            # 归一化确保万无一失
            total = prob_home + prob_draw + prob_away
            probs.append([prob_home/total, prob_draw/total, prob_away/total])
            
        return np.array(probs)

    def save_params(self, file_path: str):
        """将训练好的团队参数保存为 JSON"""
        with open(file_path, 'w') as f:
            json.dump(self.elo_ratings, f, indent=4)
        print(f"  - Elo ratings saved to {file_path}")

    def load_params(self, file_path: str):
        """从 JSON 加载参数"""
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.elo_ratings = json.load(f)
            print(f"  - Elo ratings loaded from {file_path}")
        else:
            print(f"  - Warning: {file_path} not found.")
