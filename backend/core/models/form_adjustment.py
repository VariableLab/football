"""近期状态修正模型 — 根据近 N 场战绩计算加权状态因子。"""

from core.context import TeamContext


class FormAdjustmentModel:
    """根据近 N 场战绩（W/D/L 字符串）计算加权状态因子。"""

    @classmethod
    def compute_factor(cls, team: TeamContext) -> float:
        if not team.recent_results:
            return 1.0

        results = team.recent_results[-10:]
        n = len(results)
        if n == 0:
            return 1.0

        weights = [0.5 + 0.5 * (i / max(n - 1, 1)) for i in range(n)]
        points_map = {"W": 3, "D": 1, "L": 0}
        points = [points_map.get(r.upper(), 1) for r in results]

        weighted_avg = sum(p * w for p, w in zip(points, weights)) / sum(weights)
        return max(0.75, min(1.15, 0.85 + 0.10 * (weighted_avg / 1.5)))
