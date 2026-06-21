"""
重尾泊松模型 — 解决标准泊松尾部衰减过快的问题。

标准泊松假设 Var(进球) = E(进球)，但现实中：
- 强弱悬殊比赛: Var >> Mean (尾部厚)
- 势均力敌比赛: Var ≈ Mean (接近泊松)

方案: 使用负二项分布 (NB) 替代泊松，通过 dispersion 参数控制尾部厚度。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import nbinom, poisson
from typing import Dict, Tuple, Optional


class HeavyTailPoisson:
    """
    重尾泊松模型。

    核心思想:
    - 当两队实力差距大时，使用更高的 dispersion 参数
    - dispersion 越大，尾部越厚，大比分概率越高

    dispersion = 1.0  → 等价于标准泊松
    dispersion > 1.0  → 尾部更厚 (适合强弱悬殊)
    dispersion < 1.0  → 尾部更薄 (适合势均力敌)
    """

    @staticmethod
    def compute_dispersion(
        elo_diff: float,
        xg_diff: float,
        market_variance: float,
    ) -> float:
        """
        根据比赛特征计算 dispersion 参数。

        dispersion 越大 → 尾部越厚 → 大比分概率越高

        经验校准:
        - Elo 差 0: dispersion = 1.0
        - Elo 差 250: dispersion = 1.8 (荷兰vs瑞典级别)
        - Elo 差 400+: dispersion = 2.5+
        """
        abs_diff = abs(elo_diff)

        # Elo 差距 → dispersion (非线性增长)
        if abs_diff < 50:
            elo_disp = 1.0
        elif abs_diff < 150:
            elo_disp = 1.0 + (abs_diff - 50) / 100.0 * 0.3
        elif abs_diff < 300:
            elo_disp = 1.3 + (abs_diff - 150) / 150.0 * 0.7  # 加大增幅
        elif abs_diff < 500:
            elo_disp = 2.0 + (abs_diff - 300) / 200.0 * 0.8
        else:
            elo_disp = 2.8 + min(1.0, (abs_diff - 500) / 300.0 * 0.5)

        # xG 差距辅助
        xg_disp = 1.0 + abs(xg_diff) / 1.5 * 0.15

        # 市场赔率离散度
        mkt_disp = 1.0 + max(0, market_variance) * 0.3

        # 综合 (Elo 主导)
        dispersion = 0.6 * elo_disp + 0.25 * xg_disp + 0.15 * mkt_disp

        return max(0.8, min(5.0, dispersion))

    @staticmethod
    def nb_pmf(k: int, mu: float, dispersion: float) -> float:
        """
        负二项分布 PMF (参数化为均值 mu + dispersion)。

        NB(r, p) 的均值 = r(1-p)/p = mu
        NB(r, p) 的方差 = mu + mu²/r = mu * (1 + mu/r)

        令 dispersion = 1 + mu/r → r = mu / (dispersion - 1)
        """
        if dispersion <= 1.0:
            # 退化为标准泊松
            return poisson.pmf(k, mu)

        r = mu / (dispersion - 1.0)
        p = 1.0 / dispersion

        if r <= 0 or p <= 0 or p >= 1:
            return poisson.pmf(k, mu)

        return nbinom.pmf(k, r, p)

    @classmethod
    def predict_score_matrix(
        cls,
        lambda_h: float,
        lambda_a: float,
        elo_diff: float = 0,
        xg_diff: float = 0,
        market_variance: float = 0.0,
        max_goals: int = 8,
    ) -> Tuple[np.ndarray, float, float]:
        """
        返回重尾比分概率矩阵。

        关键改进: 主队和客队使用不同的 dispersion。
        - 强队 (lambda 高): 使用较高 dispersion → 可能大比分
        - 弱队 (lambda 低): 使用较低 dispersion → 不太可能进很多球
        这样 0:0 不会异常升高，但 4:0, 5:0 会合理上升。
        """
        base_dispersion = cls.compute_dispersion(elo_diff, xg_diff, market_variance)

        # 强队 dispersion 更高 (可能打出大比分)
        # 弱队 dispersion 更低 (进球少，尾部薄)
        disp_h = min(base_dispersion * 1.5, 4.0)  # 主队
        disp_a = max(base_dispersion * 0.7, 1.0)  # 客队

        size = max_goals + 1
        matrix = np.zeros((size, size))

        for i in range(size):
            for j in range(size):
                prob_i = cls.nb_pmf(i, lambda_h, disp_h)
                prob_j = cls.nb_pmf(j, lambda_a, disp_a)
                matrix[i][j] = prob_i * prob_j

        total = matrix.sum()
        if total > 0:
            matrix /= total

        return matrix, lambda_h, lambda_a

    @classmethod
    def predict_spf(
        cls,
        lambda_h: float,
        lambda_a: float,
        elo_diff: float = 0,
        xg_diff: float = 0,
        market_variance: float = 0.0,
        max_goals: int = 8,
    ) -> Dict[str, float]:
        """只计算胜平负概率 (快速模式)。"""
        matrix, lh, la = cls.predict_score_matrix(
            lambda_h, lambda_a, elo_diff, xg_diff, market_variance, max_goals
        )

        p_home = sum(matrix[i][j] for i in range(max_goals+1) for j in range(max_goals+1) if i > j)
        p_draw = sum(matrix[i][j] for i in range(max_goals+1) for j in range(max_goals+1) if i == j)
        p_away = sum(matrix[i][j] for i in range(max_goals+1) for j in range(max_goals+1) if i < j)

        total = p_home + p_draw + p_away
        return {
            "home": p_home / total if total > 0 else 1/3,
            "draw": p_draw / total if total > 0 else 1/3,
            "away": p_away / total if total > 0 else 1/3,
        }

    @classmethod
    def detect_upset_risk(
        cls,
        lambda_h: float,
        lambda_a: float,
        model_spf: Dict[str, float],
        market_spf: Dict[str, float],
    ) -> Dict[str, float]:
        """
        检测爆冷概率。

        核心逻辑:
        - 当市场赔率隐含概率与模型概率差异大时 → 爆冷风险高
        - 使用 KL 散度量化两个分布的差异

        返回:
            {upset_risk: float, divergence: float, direction: str}
        """
        # KL 散度: D(model || market)
        kl_divergence = 0.0
        for outcome in ["home", "draw", "away"]:
            m = model_spf.get(outcome, 1/3)
            k = market_spf.get(outcome, 1/3)
            if m > 0 and k > 0:
                kl_divergence += m * np.log(m / k)

        # 爆冷概率: KL 散度越大，爆冷可能性越高
        # 经验公式: P(upset) = sigmoid(KL * 10 - 1)
        upset_risk = 1.0 / (1.0 + np.exp(-(kl_divergence * 10 - 1.0)))

        # 爆冷方向
        direction = ""
        if model_spf["home"] > market_spf["home"] + 0.15:
            direction = "away_upset"  # 模型看好主队，市场不看好 → 客胜爆冷
        elif model_spf["away"] > market_spf["away"] + 0.15:
            direction = "home_upset"  # 模型看好客队，市场不看好 → 主胜爆冷
        else:
            direction = "normal"

        return {
            "upset_risk": round(float(upset_risk), 4),
            "divergence": round(float(kl_divergence), 4),
            "direction": direction,
        }
