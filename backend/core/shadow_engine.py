"""
影子物理一致性概率引擎 - 影子系统 (v3.0_shadow)
"""
from typing import Dict, Any, Tuple
import numpy as np
from scipy.stats import poisson

# 延迟导入以防止循环依赖
def get_poisson_model():
    from core.prediction_engine import PoissonModel
    return PoissonModel

class ShadowPredictor:
    """
    影子预测引擎：从归一化的比分物理矩阵出发，通过积分位移推导所有衍生玩法，
    并能够支持动态传入真实 handicap，以确保概率 100% 对齐。
    """

    @classmethod
    def predict(cls, ctx, real_handicap: int = 0, target_spf: Dict[str, float] = None, custom_lambdas: Tuple[float, float] = None) -> Dict[str, Any]:
        """
        根据 MatchContext 与真实的让球数，推导全玩法的物理一致性概率。
        可选传入 target_spf 作为胜平负硬约束进行矩阵拉伸对齐。
        可选传入 custom_lambdas 使用自定义（如深度学习预测）的期望进球参数。
        """
        PoissonModel = get_poisson_model()
        from core.prediction_engine import DRAW_INFLATION_FACTOR, MAX_GOALS
        
        # 1. 获取 Dixon-Coles 归一化比分矩阵
        if custom_lambdas:
            matrix, lambda_h, lambda_a = PoissonModel.predict_score_matrix_with_lambdas(custom_lambdas[0], custom_lambdas[1])
        else:
            matrix, lambda_h, lambda_a = PoissonModel.predict_score_matrix(ctx)
        size = matrix.shape[0]

        # ─── v3.5 矩阵拉伸对齐 ───
        if target_spf:
            s_home = sum(matrix[i][j] for i in range(size) for j in range(size) if i > j)
            s_draw = sum(matrix[i][j] for i in range(size) for j in range(size) if i == j)
            s_away = sum(matrix[i][j] for i in range(size) for j in range(size) if i < j)
            
            t_home = target_spf.get("home", 0.3333)
            t_draw = target_spf.get("draw", 0.3333)
            t_away = target_spf.get("away", 0.3333)
            
            k_home = t_home / s_home if s_home > 0 else 0.0
            k_draw = t_draw / s_draw if s_draw > 0 else 0.0
            k_away = t_away / s_away if s_away > 0 else 0.0
            
            for i in range(size):
                for j in range(size):
                    if i > j:
                        matrix[i][j] *= k_home
                    elif i == j:
                        matrix[i][j] *= k_draw
                    else:
                        matrix[i][j] *= k_away
            
            total_sum = matrix.sum()
            if total_sum > 0:
                matrix /= total_sum

        # 2. 胜平负 (SPF)
        p_home = float(sum(matrix[i][j] for i in range(size) for j in range(size) if i > j))
        p_draw = float(sum(matrix[i][j] for i in range(size) for j in range(size) if i == j))
        p_away = float(sum(matrix[i][j] for i in range(size) for j in range(size) if i < j))

        if not target_spf:
            p_draw *= DRAW_INFLATION_FACTOR
            
        spf_total = p_home + p_draw + p_away
        if spf_total <= 0:
            spf_total = 1.0
            
        spf = {
            "home": round(p_home / spf_total, 4),
            "draw": round(p_draw / spf_total, 4),
            "away": round(p_away / spf_total, 4)
        }

        # 3. 比分 (SCORE)
        inflated_matrix = np.copy(matrix)
        if not target_spf:
            for i in range(size):
                inflated_matrix[i][i] *= DRAW_INFLATION_FACTOR
        inflated_sum = inflated_matrix.sum()
        if inflated_sum > 0:
            inflated_matrix /= inflated_sum

        score = {}
        for i in range(size):
            for j in range(size):
                key = f"{i}:{j}" if i < MAX_GOALS and j < MAX_GOALS else f"{min(i, MAX_GOALS)}+:{min(j, MAX_GOALS)}+"
                prob = float(inflated_matrix[i][j])
                if prob > 0.005: # 影子系统降低门槛至 0.5%，保留更多长尾比分
                    score[key] = round(prob, 4)

        # 4. 让球胜平负 (RQ)：根据真实传入的 real_handicap 积分，彻底解决 handicap=0 的 Stacking Bug
        p_rq_home = float(sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) > real_handicap))
        p_rq_draw = float(sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) == real_handicap))
        p_rq_away = float(sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) < real_handicap))
        rq_total = p_rq_home + p_rq_draw + p_rq_away
        
        # 防止分母为 0 导致溢出
        if rq_total <= 0:
            rq_total = 1.0
            
        rq = {
            "home": round(p_rq_home / rq_total, 4),
            "draw": round(p_rq_draw / rq_total, 4),
            "away": round(p_rq_away / rq_total, 4),
            "handicap": real_handicap
        }

        # 5. 总进球 (GOALS)
        goals = {}
        for total_goals in range(7):
            prob = float(sum(matrix[i][j] for i in range(size) for j in range(size) if i + j == total_goals))
            if prob > 0.005:
                goals[str(total_goals)] = round(prob, 4)
        prob_7plus = float(sum(matrix[i][j] for i in range(size) for j in range(size) if i + j >= 7))
        if prob_7plus > 0.005:
            goals["7+"] = round(prob_7plus, 4)

        # 6. 半全场 (HALF)：上半场 ELO 时间衰减分配与转移概率
        HT_FT_TRANSITION = {
            "home": {"home": 0.785, "draw": 0.151, "away": 0.065},
            "draw": {"home": 0.442, "draw": 0.237, "away": 0.321},
            "away": {"home": 0.105, "draw": 0.199, "away": 0.697},
        }
        HT_DISTRIBUTION = {"home": 0.368, "draw": 0.364, "away": 0.268}

        # 上半场分配比例为 48%
        lambda_h_1h = lambda_h * 0.48
        lambda_a_1h = lambda_a * 0.48

        def half_outcome_prob(lh: float, la: float) -> Dict[str, float]:
            p_h, p_d, p_a = 0.0, 0.0, 0.0
            for i in range(5):
                for j in range(5):
                    pi = poisson.pmf(i, lh)
                    pj = poisson.pmf(j, la)
                    if i > j:
                        p_h += pi * pj
                    elif i == j:
                        p_d += pi * pj
                    else:
                        p_a += pi * pj
            t = p_h + p_d + p_a
            if t <= 0:
                t = 1.0
            return {"home": p_h / t, "draw": p_d / t, "away": p_a / t}

        half_1h = half_outcome_prob(lambda_h_1h, lambda_a_1h)
        # 混合 50% 历史先验，平滑局部极端噪音
        for k in half_1h:
            half_1h[k] = 0.5 * half_1h[k] + 0.5 * HT_DISTRIBUTION[k]

        half = {}
        outcomes = ["home", "draw", "away"]
        labels = {
            "homehome": "主主", "homedraw": "主平", "homeaway": "主客",
            "drawhome": "平主", "drawdraw": "平平", "drawaway": "平客",
            "awayhome": "客主", "awaydraw": "客平", "awayaway": "客客"
        }
        
        for h1 in outcomes:
            for h2 in outcomes:
                key = f"{h1}{h2}"
                prob = half_1h[h1] * HT_FT_TRANSITION[h1][h2]
                half[labels.get(key, key)] = round(prob, 4)

        return {
            "spf": spf,
            "rq": rq,
            "score": score,
            "goals": goals,
            "half": half
        }
