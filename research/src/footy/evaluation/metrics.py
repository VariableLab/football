import numpy as np
import pandas as pd

def calculate_rps(probs: np.ndarray, outcomes: pd.Series) -> float:
    """
    计算 Ranked Probability Score (RPS)。
    RPS 是足球预测的学术标准，它考虑了结果的顺序性（虽然 H/D/A 的顺序有争议，但通常按此评估）。
    :param probs: (n, 3) 概率分布 [P(H), P(D), P(A)]
    :param outcomes: 实际结果 Series ('H', 'D', 'A')
    """
    n = len(outcomes)
    rps_list = []
    
    # 映射结果到向量
    outcome_map = {'H': [1, 0, 0], 'D': [0, 1, 0], 'A': [0, 0, 1]}
    
    for i in range(n):
        p = probs[i]
        o = np.array(outcome_map[outcomes.iloc[i]])
        
        # 累积分布
        cp = np.cumsum(p)
        co = np.cumsum(o)
        
        rps = np.sum((cp - co)**2) / (len(p) - 1)
        rps_list.append(rps)
        
    return np.mean(rps_list)

def calculate_brier_score(probs: np.ndarray, outcomes: pd.Series) -> float:
    """
    计算 Brier Score。衡量概率预测的校准度。
    """
    n = len(outcomes)
    outcome_map = {'H': [1, 0, 0], 'D': [0, 1, 0], 'A': [0, 0, 1]}
    
    brier_sum = 0
    for i in range(n):
        p = probs[i]
        o = np.array(outcome_map[outcomes.iloc[i]])
        brier_sum += np.sum((p - o)**2)
        
    return brier_sum / (n * 3.0) # 标准化到 [0, 1]

def calculate_accuracy(probs: np.ndarray, outcomes: pd.Series) -> float:
    """
    基础准确率。
    """
    pred_outcomes = []
    for p in probs:
        idx = np.argmax(p)
        pred_outcomes.append(['H', 'D', 'A'][idx])
    
    return (np.array(pred_outcomes) == outcomes.values).mean()
