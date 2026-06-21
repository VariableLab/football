"""
异常比分检测与修正层

核心思路:
1. 标准泊松模型给出基础比分概率
2. 异常检测器判断这场比赛是否"可能出大比分"
3. 如果可能，对基础概率分布进行"尾部重分配"

尾部重分配策略:
- 从最可能的比分 (如 2:0, 1:0) 抽取少量概率
- 分配给大比分 (如 4:0, 5:1, 5:0)
- 抽取的比例由 Elo 差距 + 市场信号决定

这样 0:0 不会异常升高，但 4:0, 5:1 等极端比分会有合理概率。
"""
from __future__ import annotations

import numpy as np
from typing import Dict, Tuple


class OutlierDetector:
    """
    异常比分检测器。

    当 Elo 差距 > 200 或 xG 差距 > 1.0 时，
    对标准泊松比分概率进行尾部重分配。
    """

    # 大比分集合 (主胜 >= 4 球)
    BIG_HOME_SCORES = {"4:0", "5:0", "6:0", "7:0", "8:0",
                       "4:1", "5:1", "6:1", "7:1", "8:1",
                       "4:2", "5:2", "6:2", "5:3", "6:3", "7:3"}

    # 大比分集合 (客胜 >= 4 球)
    BIG_AWAY_SCORES = {"0:4", "0:5", "0:6", "0:7", "0:8",
                       "1:4", "1:5", "1:6", "1:7", "1:8",
                       "2:4", "2:5", "2:6", "3:5", "3:6", "3:7"}

    # 中等比分 (2-3 球)
    MODERATE_SCORES = {"2:0", "3:0", "3:1", "0:2", "0:3", "1:3"}

    @classmethod
    def compute_outlier_risk(
        cls,
        elo_diff: float,
        xg_diff: float,
        market_spf: Dict[str, float],
        model_spf: Dict[str, float],
    ) -> float:
        """
        计算本场比赛出现异常比分的概率。

        返回 0.0 ~ 1.0，值越高 → 越可能出大比分。
        """
        risk = 0.0

        # 1. Elo 差距 (主要信号)
        if abs(elo_diff) > 300:
            risk += 0.4
        elif abs(elo_diff) > 200:
            risk += 0.25
        elif abs(elo_diff) > 100:
            risk += 0.1

        # 2. xG 差距
        if abs(xg_diff) > 1.5:
            risk += 0.2
        elif abs(xg_diff) > 1.0:
            risk += 0.1

        # 3. 模型与市场分歧 (KL 散度近似)
        kl = 0.0
        for k in ["home", "draw", "away"]:
            m = model_spf.get(k, 1/3)
            mk = market_spf.get(k, 1/3)
            if m > 0 and mk > 0:
                kl += m * np.log(m / mk)
        if kl > 0.1:
            risk += 0.15
        elif kl > 0.05:
            risk += 0.08

        return min(1.0, max(0.0, risk))

    @classmethod
    def redistribute_tail(
        cls,
        score_probs: Dict[str, float],
        outlier_risk: float,
        home_lambda: float,
        away_lambda: float,
    ) -> Dict[str, float]:
        """
        根据异常风险，对比分概率进行尾部重分配。

        当 outlier_risk > 0 时:
        1. 从常见比分 (1:0, 2:0, 2:1) 抽取少量概率
        2. 分配给大比分 (4:0, 5:0, 5:1)

        抽取比例 = outlier_risk * 抽取系数
        分配比例 = 抽取总量 * 大比分权重

        大比分权重:
        - 强队 λ 高 → 大比分主要在强队方向
        - 弱队 λ 低 → 大比分概率自然低
        """
        if outlier_risk <= 0.05:
            return score_probs  # 不需要重分配

        # 1. 确定哪边是强队
        strong_side = "home" if home_lambda > away_lambda else "away"

        # 2. 从常见比分中抽取
        source_scores = ["1:0", "2:0", "2:1", "1:1", "0:1"]
        total_extracted = 0.0
        extracted = {}

        for s in source_scores:
            if s not in score_probs:
                continue
            # 抽取比例: 常见比分抽 3-8%
            extract_rate = 0.03 + outlier_risk * 0.05
            amount = score_probs[s] * extract_rate
            if amount > 0:
                extracted[s] = amount
                total_extracted += amount

        if total_extracted <= 0.0001:
            return score_probs

        # 3. 分配给大比分
        target_scores = []
        if strong_side == "home":
            target_scores = ["4:0", "5:0", "3:0", "6:0", "5:1", "4:1", "3:1"]
        else:
            target_scores = ["0:4", "0:5", "0:3", "0:6", "1:5", "1:4", "1:3"]

        # 按原始概率比例分配 (原始概率高的获得更多)
        target_probs = {s: score_probs.get(s, 0) for s in target_scores}
        total_target = sum(target_probs.values())

        if total_target > 0:
            for s in target_scores:
                share = target_probs[s] / total_target if total_target > 0 else 1.0 / len(target_scores)
                score_probs[s] = score_probs.get(s, 0) + total_extracted * share
        else:
            # 均匀分配
            per_score = total_extracted / len(target_scores)
            for s in target_scores:
                score_probs[s] = score_probs.get(s, 0) + per_score

        # 4. 从源比分中扣除
        for s, amount in extracted.items():
            score_probs[s] = max(0.0, score_probs[s] - amount)

        # 5. 归一化
        total = sum(score_probs.values())
        if total > 0:
            score_probs = {k: v / total for k, v in score_probs.items()}

        return score_probs

    @classmethod
    def predict_with_outlier_correction(
        cls,
        std_score_probs: Dict[str, float],
        elo_diff: float,
        xg_diff: float,
        market_spf: Dict[str, float],
        model_spf: Dict[str, float],
        lambda_h: float,
        lambda_a: float,
    ) -> Dict[str, float]:
        """
        完整的异常比分修正流程。

        1. 计算异常风险
        2. 进行尾部重分配
        3. 返回修正后的比分概率
        """
        outlier_risk = cls.compute_outlier_risk(
            elo_diff, xg_diff, market_spf, model_spf
        )

        if outlier_risk <= 0.05:
            return std_score_probs

        corrected = cls.redistribute_tail(
            dict(std_score_probs), outlier_risk, lambda_h, lambda_a
        )

        return corrected
