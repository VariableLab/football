"""
爆冷探测器 (Upset Detector)

核心思路: 比较模型概率与市场隐含概率之间的 KL 散度，
衡量"模型认为"和"市场认为"的分歧程度。

KL 散度越大 → 模型与市场对同一场比赛的看法差异越大 → 爆冷可能性越高

用法:
    detector = UpsetDetector()
    result = detector.detect(
        model_spf={"home": 0.60, "draw": 0.25, "away": 0.15},
        market_spf={"home": 0.45, "draw": 0.30, "away": 0.25},
    )
    # result.upset_probability = 0.35  (35% 爆冷概率)
"""
from __future__ import annotations

import math
import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class UpsetSignal:
    """爆冷检测结果"""
    kl_divergence: float       # KL(模型||市场)
    upset_probability: float   # 归一化后的爆冷概率 (0~1)
    divergence_direction: str  # "model_home_favor" / "model_away_favor" / "model_draw_favor"
    is_upset_candidate: bool   # 是否值得关注的爆冷机会
    confidence: str            # high / medium / low


class UpsetDetector:
    """
    基于 KL 散度的爆冷探测器。

    工作原理:
    1. 计算模型概率分布 P 和市场隐含概率 Q 之间的 KL 散度
    2. KL 散度越大 → 模型与市场预期分歧越大
    3. 根据分歧方向和幅度推断爆冷可能性
    """

    # KL 散度阈值
    KL_LOW = 0.001      # 微小分歧
    KL_MEDIUM = 0.01    # 中等分歧
    KL_HIGH = 0.03      # 高分歧

    # 爆冷概率上限
    MAX_UPSET_PROB = 0.65

    def detect(
        self,
        model_spf: Dict[str, float],
        market_spf: Dict[str, float],
        model_score: Optional[Dict[str, float]] = None,
        market_score: Optional[Dict[str, float]] = None,
    ) -> UpsetSignal:
        """
        检测爆冷可能性。

        Args:
            model_spf: 模型 SPF 概率 {"home": 0.55, "draw": 0.25, "away": 0.20}
            market_spf: 市场隐含概率 (从赔率反推)
            model_score: 模型比分概率 (可选，增加检测维度)
            market_score: 市场比分隐含概率 (可选)

        Returns:
            UpsetSignal
        """
        # 1. 计算 SPF KL 散度
        kl_spf = self._kl_divergence(model_spf, market_spf)

        # 2. 计算比分 KL 散度 (如果有)
        kl_score = 0.0
        if model_score and market_score:
            kl_score = self._kl_divergence(model_score, market_score)

        # 3. 综合 KL 散度 (SPF 权重 0.7, Score 权重 0.3)
        kl_total = 0.7 * kl_spf + 0.3 * kl_score

        # 4. 判断分歧方向
        direction = self._divergence_direction(model_spf, market_spf)

        # 5. 归一化为爆冷概率
        upset_prob = self._kl_to_upset_probability(kl_total)

        # 6. 确定置信度
        if kl_total > self.KL_HIGH:
            conf = "high"
        elif kl_total > self.KL_MEDIUM:
            conf = "medium"
        else:
            conf = "low"

        return UpsetSignal(
            kl_divergence=round(kl_total, 6),
            upset_probability=round(upset_prob, 4),
            divergence_direction=direction,
            is_upset_candidate=upset_prob > 0.15 and kl_total > self.KL_MEDIUM,
            confidence=conf,
        )

    @staticmethod
    def _kl_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
        """
        计算 KL 散度: KL(P||Q) = sum P(x) * log(P(x)/Q(x))

        加入 epsilon 平滑避免除零和对数奇点。
        """
        epsilon = 1e-8

        # 确保两个分布覆盖相同的 key
        all_keys = set(list(p.keys()) + list(q.keys()))

        kl = 0.0
        for key in all_keys:
            pk = max(p.get(key, 0), epsilon)
            qk = max(q.get(key, 0), epsilon)
            kl += pk * math.log(pk / qk)

        return max(kl, 0.0)

    @staticmethod
    def _divergence_direction(
        model: Dict[str, float],
        market: Dict[str, float],
    ) -> str:
        """判断分歧方向"""
        model_home = model.get("home", 0)
        market_home = market.get("home", 0)

        diff_home = model_home - market_home
        diff_draw = model.get("draw", 0) - market.get("draw", 0)
        diff_away = model.get("away", 0) - market.get("away", 0)

        max_diff = max(abs(diff_home), abs(diff_draw), abs(diff_away))

        if max_diff == abs(diff_home):
            if diff_home > 0:
                return "model_home_favor"
            return "model_away_favor"
        elif max_diff == abs(diff_draw):
            return "model_draw_favor"
        else:
            if diff_away > 0:
                return "model_away_favor"
            return "model_home_favor"

    @staticmethod
    def _kl_to_upset_probability(kl: float) -> float:
        """
        将 KL 散度转换为爆冷概率。

        使用 sigmoid 函数: P = 1 / (1 + exp(-a*(KL - b)))
        """
        if kl < 1e-10:
            return 0.0

        # sigmoid 参数调整
        a = 20.0   # 斜率
        b = 0.005  # 拐点

        prob = 1.0 / (1.0 + math.exp(-a * (kl - b)))
        return min(prob * 2.0, 0.65)  # 缩放并封顶

    @classmethod
    def detect_from_odds(
        cls,
        model_spf: Dict[str, float],
        odds_home: float,
        odds_draw: float,
        odds_away: float,
    ) -> UpsetSignal:
        """
        便捷方法: 直接从赔率计算市场隐含概率并检测。

        适用场景: 前端传入了模型概率 + 实时赔率。
        """
        market_spf = cls._odds_to_market_probs(odds_home, odds_draw, odds_away)
        detector = cls()
        return detector.detect(model_spf, market_spf)

    @staticmethod
    def _odds_to_market_probs(
        odds_home: float,
        odds_draw: float,
        odds_away: float,
    ) -> Dict[str, float]:
        """从赔率计算市场隐含概率 (去除抽水)"""
        implied = [
            1.0 / max(odds_home, 1.01),
            1.0 / max(odds_draw, 1.01),
            1.0 / max(odds_away, 1.01),
        ]
        total = sum(implied)
        if total <= 0:
            return {"home": 1/3, "draw": 1/3, "away": 1/3}
        return {
            "home": implied[0] / total,
            "draw": implied[1] / total,
            "away": implied[2] / total,
        }
