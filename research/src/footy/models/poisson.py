import numpy as np
import pandas as pd
import json
import os
from scipy.optimize import minimize
from scipy.stats import poisson
from .base import BasePredictor

class PoissonPredictor(BasePredictor):
    """
    专家级泊松回归模型 (基础实现)。
    计算球队攻击/防守强度，并预测比分分布。
    """
    def __init__(self):
        self.team_params = {} # {team: {'att': x, 'def': y}}
        self.avg_home_goals = 1.5
        self.avg_away_goals = 1.2

    def _loss_function(self, params, teams, home_idx, away_idx, home_goals, away_goals):
        att = params[:len(teams)]
        defe = params[len(teams):]
        l_h = att[home_idx] * defe[away_idx]
        l_a = att[away_idx] * defe[home_idx]
        log_lik = np.sum(poisson.logpmf(home_goals, l_h)) + np.sum(poisson.logpmf(away_goals, l_a))
        return -log_lik

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        if 'FTHG' not in X.columns:
            return
        teams = sorted(list(set(X['HomeTeam']) | set(X['AwayTeam'])))
        team_to_idx = {t: i for i, t in enumerate(teams)}
        home_idx = X['HomeTeam'].map(team_to_idx).values
        away_idx = X['AwayTeam'].map(team_to_idx).values
        h_goals = X['FTHG'].values
        a_goals = X['FTAG'].values
        init_params = np.ones(2 * len(teams))
        cons = ({'type': 'eq', 'fun': lambda x: np.mean(x[:len(teams)]) - 1})
        res = minimize(self._loss_function, init_params, 
                       args=(teams, home_idx, away_idx, h_goals, a_goals),
                       constraints=cons, method='SLSQP')
        if res.success:
            att = res.x[:len(teams)]
            defe = res.x[len(teams):]
            for i, team in enumerate(teams):
                self.team_params[team] = {'att': att[i], 'def': defe[i]}

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        probs = []
        for _, row in X.iterrows():
            h_params = self.team_params.get(row['HomeTeam'], {'att': 1.0, 'def': 1.0})
            a_params = self.team_params.get(row['AwayTeam'], {'att': 1.0, 'def': 1.0})
            l_h = h_params['att'] * a_params['def']
            l_a = a_params['att'] * h_params['def']
            max_goals = 6
            h_prob = poisson.pmf(range(max_goals), l_h)
            a_prob = poisson.pmf(range(max_goals), l_a)
            m = np.outer(h_prob, a_prob)
            p_h = np.sum([m[i, j] for i in range(max_goals) for j in range(max_goals) if i > j])
            p_d = np.sum([m[i, j] for i in range(max_goals) for j in range(max_goals) if i == j])
            p_a = np.sum([m[i, j] for i in range(max_goals) for j in range(max_goals) if i < j])
            total = p_h + p_d + p_a
            probs.append([p_h/total, p_d/total, p_a/total])
        return np.array(probs)

    def save_params(self, file_path: str):
        """将训练好的团队参数保存为 JSON"""
        with open(file_path, 'w') as f:
            json.dump(self.team_params, f, indent=4)
        print(f"  - Parameters saved to {file_path}")

    def load_params(self, file_path: str):
        """从 JSON 加载参数"""
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.team_params = json.load(f)
            print(f"  - Parameters loaded from {file_path}")
        else:
            print(f"  - Warning: {file_path} not found.")
