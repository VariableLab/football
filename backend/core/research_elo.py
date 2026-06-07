import numpy as np
import pandas as pd
import json
import os
from typing import Optional
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

    def _get_rating(self, team_name: str) -> Optional[float]:
        """获取球队评分，支持大小写模糊匹配"""
        if not team_name:
            return None
            
        # 1. 精确匹配
        if team_name in self.elo_ratings:
            return self.elo_ratings[team_name]
            
        # 2. 大小写不敏感匹配
        lookup = {k.lower(): v for k, v in self.elo_ratings.items()}
        if team_name.lower() in lookup:
            return lookup[team_name.lower()]
            
        return None

    def _expected_score(self, rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """
        按时间顺序更新所有球队的 Elo 评分。
        """
        self.elo_ratings = {} # 重置
        data = X.copy()
        data['FTR'] = y
        
        for _, row in data.iterrows():
            home, away = row['HomeTeam'], row['AwayTeam']
            outcome = row['FTR']
            
            # 使用 HA 计算预期
            r_h_base = self._get_rating(home) or self.default_elo
            r_a_base = self._get_rating(away) or self.default_elo
            
            r_h = r_h_base + self.home_advantage
            r_a = r_a_base
            
            e_h = self._expected_score(r_h, r_a)
            e_a = 1 - e_h
            
            s_h = 1.0 if outcome == 'H' else (0.5 if outcome == 'D' else 0.0)
            s_a = 1.0 - s_h
            
            # 更新评分
            self.elo_ratings[home] = r_h_base + self.k_factor * (s_h - e_h)
            self.elo_ratings[away] = r_a_base + self.k_factor * (s_a - e_a)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        probs = []
        for _, row in X.iterrows():
            r_h_raw = self._get_rating(row['HomeTeam'])
            r_a_raw = self._get_rating(row['AwayTeam'])
            
            # 💡 核心改进：如果专家库里没这队，直接返回 None，不准瞎猜
            if r_h_raw is None or r_a_raw is None:
                probs.append(None)
                continue
                
            r_h = r_h_raw + self.home_advantage
            r_a = r_a_raw
            
            # 预期得分 E
            e_h = self._expected_score(r_h, r_a)
            
            # 动态平局模型
            diff = abs(r_h - r_a)
            prob_draw = 0.26 * np.exp(-diff / 500.0)
            
            # 剩余概率按预期得分比例分配
            rem = 1.0 - prob_draw
            prob_home = e_h * rem
            prob_away = (1 - e_h) * rem
            
            # 归一化
            total = prob_home + prob_draw + prob_away
            probs.append([prob_home/total, prob_draw/total, prob_away/total])
            
        return np.array(probs, dtype=object)

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
