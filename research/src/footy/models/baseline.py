import numpy as np
import pandas as pd
from .base import BasePredictor

class HomeWinBaseline(BasePredictor):
    """
    基准模型 1: 永远预测主队获胜。
    用于验证模型是否比最简单的常识更强。
    """
    def fit(self, X, y):
        pass
    
    def predict_proba(self, X):
        # 永远返回 [1.0, 0.0, 0.0]
        return np.tile([1.0, 0.0, 0.0], (len(X), 1))

class HistoricalFrequencyBaseline(BasePredictor):
    """
    基准模型 2: 基于历史频率预测。
    计算训练集中 H/D/A 的整体比例作为预测概率。
    """
    def __init__(self):
        self.probs = [0.45, 0.25, 0.30] # 默认足球大致比例
        
    def fit(self, X, y):
        counts = y.value_counts(normalize=True)
        # 确保顺序是 H, D, A
        self.probs = [counts.get('H', 0.45), counts.get('D', 0.25), counts.get('A', 0.30)]
    
    def predict_proba(self, X):
        return np.tile(self.probs, (len(X), 1))
