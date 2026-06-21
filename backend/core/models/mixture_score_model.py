"""
混合比分模型 (Mixture Score Model)

核心思路: 每场比赛有两种"状态"，模型根据比赛特征预测进入哪种状态的概率。

状态 A: 正常状态 (Normal)
- 泊松分布，λ 由实力差距决定
- 比分集中在 1:0, 2:0, 2:1
- 权重 = P(normal_state)

状态 B: 崩盘状态 (Collapse)
- 强队 λ 翻倍，弱队 λ 减半
- 比分集中在 4:0, 5:0, 5:1, 6:0
- 权重 = P(collapse_state)

最终比分概率 = w_A × P_A(score) + w_B × P_B(score)

崩盘触发信号:
1. Elo 差距 > 200 (实力悬殊)
2. 一方近期状态极差 (form_factor < 0.85)
3. 核心伤停 > 2 人
4. 赛程密度极高 (rest_days < 3)
5. 战术相克 (attack vs counter 大开大合)
"""
from __future__ import annotations

import math
import numpy as np
from scipy.stats import poisson
from typing import Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class StateParams:
    """某一状态的 λ 参数"""
    lambda_home: float
    lambda_away: float
    weight: float  # 该状态的先验概率


class MixtureScoreModel:
    """
    混合比分模型。

    用法:
        model = MixtureScoreModel()
        result = model.predict(
            lambda_h_normal=2.1, lambda_a_normal=0.8,
            home_elo=1900, away_elo=1650,
            home_form=0.9, away_form=1.1,
            home_injuries=2, home_rest_days=3,
            ...
        )
    """

    @classmethod
    def compute_collapse_probability(
        cls,
        elo_diff: float,
        xg_diff: float,
        home_form: float,
        away_form: float,
        home_injuries: int,
        away_injuries: int,
        home_rest_days: int,
        away_rest_days: int,
        tactical_clash: str = "none",  # "attack_counter", "attack_defense", etc.
    ) -> float:
        """
        计算本场比赛进入"崩盘状态"的概率。

        返回 0.0 ~ 1.0。值越高 → 越可能出大比分。
        """
        risk = 0.0

        # 1. Elo 差距 (最强信号)
        if abs(elo_diff) > 350:
            risk += 0.20
        elif abs(elo_diff) > 250:
            risk += 0.15
        elif abs(elo_diff) > 150:
            risk += 0.08
        elif abs(elo_diff) > 80:
            risk += 0.03

        # 2. 状态因子差距 (一方极差 → 崩盘)
        form_gap = abs(home_form - away_form)
        if form_gap > 0.3:
            risk += 0.10
        elif form_gap > 0.15:
            risk += 0.05

        # 3. 伤停
        total_injuries = home_injuries + away_injuries
        if total_injuries >= 3:
            risk += 0.06
        elif total_injuries >= 2:
            risk += 0.04
        elif total_injuries >= 1:
            risk += 0.02

        # 4. 赛程密度
        min_rest = min(home_rest_days, away_rest_days)
        if min_rest <= 2:
            risk += 0.06
        elif min_rest <= 4:
            risk += 0.03

        # 5. 战术相克
        if tactical_clash in ("attack_counter", "attack_attack"):
            risk += 0.05

        # 6. xG 差距 (进攻预期悬殊)
        if abs(xg_diff) > 1.5:
            risk += 0.06
        elif abs(xg_diff) > 1.0:
            risk += 0.04

        # 归一化到 [0, 1] — 用 sigmoid 阻尼防止信号叠加过高
        raw = min(0.85, max(0.0, risk))
        # sigmoid: 将 raw 压缩到更温和的范围，拐点设在 0.4
        return 1.0 / (1.0 + math.exp(-6.0 * (raw - 0.4)))

    @classmethod
    def collapse_lambdas(
        cls,
        lambda_h_normal: float,
        lambda_a_normal: float,
        collapse_prob: float,
    ) -> Tuple[float, float]:
        """
        根据崩盘概率，计算崩盘状态下的 λ。

        崩盘时:
        - 强队 λ 适度增加 (最多 ×1.3)
        - 弱队 λ 进一步削弱 (最多 ×0.4)
        - 但不会让强队 λ 超过 6.0 (避免过度极端)

        关键区别: collapse 不是简单乘以系数，
        而是向"一边倒"的方向偏移。
        """
        # 确定强队方向
        if lambda_h_normal >= lambda_a_normal:
            strong = "home"
        else:
            strong = "away"

        # 崩盘偏移量: collapse_prob 越高，偏移越大
        # 强队额外 +lambda*0.3*prob, 弱队削减 -lambda*0.6*prob
        boost = lambda_h_normal * 0.3 * collapse_prob  # 最大 +30%
        penalty = lambda_a_normal * 0.6 * collapse_prob  # 最大 -60%

        if strong == "home":
            lambda_h_collapse = min(lambda_h_normal + boost, 6.0)
            lambda_a_collapse = max(lambda_a_normal - penalty, 0.05)
        else:
            lambda_h_collapse = max(lambda_h_normal - penalty, 0.05)
            lambda_a_collapse = min(lambda_a_normal + boost, 6.0)

        return round(lambda_h_collapse, 4), round(lambda_a_collapse, 4)

    @classmethod
    def predict(
        cls,
        lambda_h: float,
        lambda_a: float,
        collapse_prob: float,
        max_goals: int = 8,
    ) -> Dict[str, float]:
        """
        混合比分模型预测。

        返回比分概率字典 {score: prob}。
        """
        if collapse_prob < 0.05:
            # 不需要混合，直接用标准泊松
            return cls._poisson_scores(lambda_h, lambda_a, max_goals)

        # 计算崩盘状态下的 λ
        lh_c, la_c = cls.collapse_lambdas(lambda_h, lambda_a, collapse_prob)

        # 正常状态得分
        normal_scores = cls._poisson_scores(lambda_h, lambda_a, max_goals)
        # 崩盘状态得分
        collapse_scores = cls._poisson_scores(lh_c, la_c, max_goals)

        # 混合
        result = {}
        all_scores = set(list(normal_scores.keys()) + list(collapse_scores.keys()))

        for score in all_scores:
            p_normal = normal_scores.get(score, 0)
            p_collapse = collapse_scores.get(score, 0)
            result[score] = (1 - collapse_prob) * p_normal + collapse_prob * p_collapse

        # 归一化
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}

        return result

    @staticmethod
    def _poisson_scores(lambda_h: float, lambda_a: float, max_goals: int) -> Dict[str, float]:
        """标准泊松比分概率"""
        scores = {}
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                ph = poisson.pmf(i, lambda_h)
                pj = poisson.pmf(j, lambda_a)
                prob = ph * pj
                if prob > 0.003:
                    key = f"{i}:{j}" if i < max_goals and j < max_goals else f"{min(i, max_goals)}+:{min(j, max_goals)}+"
                    scores[key] = prob
        return scores

    @classmethod
    def predict_with_context(
        cls,
        ctx,
        lambda_h: float,
        lambda_a: float,
        max_goals: int = 8,
    ) -> Dict[str, float]:
        """
        从 MatchContext 中提取所有特征，计算混合比分。
        """
        collapse_prob = cls.compute_collapse_probability(
            elo_diff=ctx.home_team.elo - ctx.away_team.elo,
            xg_diff=(
                (ctx.home_team.avg_xg or ctx.home_team.avg_goals_scored)
                - (ctx.away_team.avg_xg or ctx.away_team.avg_goals_conceded)
            ),
            home_form=ctx.home_team.form_factor,
            away_form=ctx.away_team.form_factor,
            home_injuries=len([x for x in (ctx.home_team.key_injuries or "").split(",") if x.strip()]),
            away_injuries=len([x for x in (ctx.away_team.key_injuries or "").split(",") if x.strip()]),
            home_rest_days=getattr(ctx.home_team, "rest_days", 7),
            away_rest_days=getattr(ctx.away_team, "rest_days", 7),
            tactical_clash=cls._detect_tactical_clash(ctx),
        )

        return cls.predict(lambda_h, lambda_a, collapse_prob, max_goals)

    @staticmethod
    def _detect_tactical_clash(ctx) -> str:
        """检测战术相克类型"""
        h_style = (ctx.home_team.tactical_style or "balanced").lower()
        a_style = (ctx.away_team.tactical_style or "balanced").lower()

        if (h_style == "attack" and a_style == "counter") or \
           (h_style == "counter" and a_style == "attack"):
            return "attack_counter"
        if h_style == "attack" and a_style == "attack":
            return "attack_attack"
        return "none"
