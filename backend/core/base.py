from abc import ABC, abstractmethod
import pandas as pd
import numpy as np

class BasePredictor(ABC):
    """
    足球预测模型基类。
    所有研究模型必须继承此类并实现抽象方法。
    """
    
    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """
        训练模型。
        :param X: 训练特征 DataFrame
        :param y: 目标变量 Series (通常是 'H', 'D', 'A')
        """
        pass
    
    @abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        预测比赛结果概率。
        :param X: 测试特征 DataFrame
        :return: np.ndarray, 形状为 (n_samples, 3)，顺序为 [P(H), P(D), P(A)]
        """
        pass
