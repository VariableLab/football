import pandas as pd
import numpy as np
from typing import List, Type
from ..models.base import BasePredictor
from ..evaluation.metrics import calculate_rps, calculate_brier_score

class TimeSeriesBacktester:
    """
    时序回测引擎。
    按时间顺序滚动训练和测试，严防数据泄漏。
    """
    def __init__(self, data: pd.DataFrame, initial_train_weeks: int = 52):
        """
        :param data: 包含 'Date' 列的完整数据集
        :param initial_train_weeks: 初始训练窗口（周）
        """
        self.data = data.sort_values('Date').reset_index(drop=True)
        self.initial_train_weeks = initial_train_weeks
        
    def run(self, model_class: Type[BasePredictor], update_interval_days: int = 7):
        """
        运行回测。
        :param model_class: 预测模型类
        :param update_interval_days: 模型重新训练的频率（天）
        """
        start_date = self.data['Date'].min() + pd.Timedelta(weeks=self.initial_train_weeks)
        end_date = self.data['Date'].max()
        
        current_date = start_date
        all_results = []
        
        model = model_class()
        
        while current_date < end_date:
            next_date = current_date + pd.Timedelta(days=update_interval_days)
            
            # 训练集: current_date 之前的所有比赛
            train_mask = self.data['Date'] < current_date
            train_df = self.data[train_mask]
            
            # 测试集: current_date 到 next_date 之间的比赛
            test_mask = (self.data['Date'] >= current_date) & (self.data['Date'] < next_date)
            test_df = self.data[test_mask]
            
            if len(test_df) > 0:
                # 重新训练模型 (在研究模式下，我们假设每次窗口滚动都可能重训)
                X_train = train_df.drop(columns=['FTR']) # 简化处理，实际需要特征工程
                y_train = train_df['FTR']
                
                model.fit(X_train, y_train)
                
                # 预测
                X_test = test_df.drop(columns=['FTR'])
                probs = model.predict_proba(X_test)
                
                # 保存结果用于评估
                test_results = test_df.copy()
                test_results['prob_H'] = probs[:, 0]
                test_results['prob_D'] = probs[:, 1]
                test_results['prob_A'] = probs[:, 2]
                all_results.append(test_results)
            
            current_date = next_date
            
        return pd.concat(all_results) if all_results else pd.DataFrame()
