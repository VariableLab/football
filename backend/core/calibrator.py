"""
概率校准器 — 修正模型概率偏差。

独立 Poisson 模型在低概率区过度自信（模型说 12%，实际 5%），
高概率区则欠自信（模型说 70%，实际 72%）。校准器用历史数据
修正这一偏差，使输出概率更接近真实命中率。

校准方法: 分段线性 (Piecewise Linear Calibration)
- 将模型概率分到若干桶 (bin)
- 每桶计算实际命中率 vs 模型平均概率
- 用校准因子修正: calibrated = model_p × factor

用法:
    from calibrator import Calibrator
    cal = Calibrator()
    calibrated = cal.calibrate_spf({"home": 0.58, "draw": 0.24, "away": 0.18})
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

import numpy as np


# ─── 默认校准曲线 (从 30K 五大联赛 walk-forward 数据拟合) ───
# 键: (桶下限, 桶上限), 值: 校准因子 = 实际命中率 / 模型平均概率
# 数据来源: 5330 场有赔率比赛, 每场 3 选项 (home/draw/away) = 15990 个观测
DEFAULT_CALIBRATION_FACTORS: Dict[Tuple[float, float], float] = {
    (0.00, 0.10): 0.70,   # 极低概率: 模型严重过度自信
    (0.10, 0.15): 0.42,   # 低概率: 过度自信最严重
    (0.15, 0.20): 0.67,
    (0.20, 0.25): 0.77,
    (0.25, 0.30): 0.90,
    (0.30, 0.35): 0.90,
    (0.35, 0.40): 0.86,
    (0.40, 0.45): 0.99,   # 交叉点: 模型开始准确
    (0.45, 0.50): 0.97,
    (0.50, 0.55): 1.04,   # 高概率: 模型欠自信
    (0.55, 0.60): 0.99,
    (0.60, 0.65): 1.01,
    (0.65, 0.70): 1.06,
    (0.70, 0.75): 1.04,
    (0.75, 0.80): 1.04,
    (0.80, 0.85): 1.09,
    (0.85, 1.00): 1.10,
}


@dataclass(frozen=True)
class CalibrationBucket:
    """单个校准桶的统计信息"""
    model_prob_range: Tuple[float, float]
    factor: float
    sample_size: int = 0
    actual_hit_rate: float = 0.0
    model_avg_prob: float = 0.0


@dataclass
class CalibrationCurve:
    """完整的校准曲线"""
    buckets: List[CalibrationBucket] = field(default_factory=list)
    source: str = "default"
    sample_size: int = 0
    brier_before: float = 0.0
    brier_after: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "sample_size": self.sample_size,
            "brier_before": round(self.brier_before, 4),
            "brier_after": round(self.brier_after, 4),
            "buckets": [
                {
                    "range": list(b.model_prob_range),
                    "factor": round(b.factor, 4),
                    "sample_size": b.sample_size,
                    "actual_hit_rate": round(b.actual_hit_rate, 4),
                    "model_avg_prob": round(b.model_avg_prob, 4),
                }
                for b in self.buckets
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class Calibrator:
    """
    概率校准器。

    用法:
        cal = Calibrator()
        calibrated = cal.calibrate_spf({"home": 0.58, "draw": 0.24, "away": 0.18})
        # => {"home": 0.5742, "draw": 0.2160, "away": 0.1260} (归一化后)
    """

    def __init__(self, curve: Optional[CalibrationCurve] = None):
        if curve is not None:
            self._factors = {
                b.model_prob_range: b.factor for b in curve.buckets
            }
            self._curve = curve
        else:
            self._factors = DEFAULT_CALIBRATION_FACTORS
            self._curve = None

    def calibrate(self, model_prob: float) -> float:
        """校准单个概率值。"""
        for (lo, hi), factor in self._factors.items():
            if lo <= model_prob < hi:
                return min(model_prob * factor, 0.999)
        # 超出范围: 不修正
        return model_prob

    def calibrate_spf(self, probs: Dict[str, float]) -> Dict[str, float]:
        """
        校准 SPF 三元概率并归一化。

        校准后三个概率之和可能不等于 1, 需要归一化。
        归一化保证总和 = 1.0, 每个概率 ≥ 0.001。
        """
        calibrated = {}
        for key, p in probs.items():
            calibrated[key] = self.calibrate(p)

        return _normalize(calibrated, floor=0.001)

    def calibrate_multi(self, probs: Dict[str, float]) -> Dict[str, float]:
        """校准任意元概率并归一化 (比分、总进球、半全场等)。"""
        calibrated = {}
        for key, p in probs.items():
            calibrated[key] = self.calibrate(p)

        return _normalize(calibrated, floor=0.0005)

    @staticmethod
    def fit_from_data(
        observations: List[Tuple[float, bool]],
        n_bins: int = 17,
    ) -> CalibrationCurve:
        """
        从历史数据拟合校准曲线。

        Args:
            observations: [(model_prob, actual_won), ...]
            n_bins: 桶数量

        Returns:
            CalibrationCurve with fitted factors
        """
        if not observations:
            return CalibrationCurve(source="empty")

        probs = np.array([o[0] for o in observations])
        outcomes = np.array([o[1] for o in observations], dtype=float)

        # 按概率排序
        sorted_idx = np.argsort(probs)
        probs_sorted = probs[sorted_idx]
        outcomes_sorted = outcomes[sorted_idx]

        # 分桶
        bucket_edges = np.linspace(0, 1, n_bins + 1)
        buckets: List[CalibrationBucket] = []

        brier_before = 0.0
        brier_after = 0.0

        for i in range(n_bins):
            lo, hi = bucket_edges[i], bucket_edges[i + 1]
            mask = (probs_sorted >= lo) & (probs_sorted < hi)
            bucket_probs = probs_sorted[mask]
            bucket_outcomes = outcomes_sorted[mask]

            if len(bucket_probs) < 5:
                factor = 1.0
                actual = 0.0
                model_avg = (lo + hi) / 2
                sample_size = len(bucket_probs)
            else:
                model_avg = float(np.mean(bucket_probs))
                actual = float(np.mean(bucket_outcomes))
                factor = actual / model_avg if model_avg > 0 else 1.0
                sample_size = len(bucket_probs)

            # 限制因子在合理范围
            factor = max(0.2, min(factor, 1.5))

            buckets.append(CalibrationBucket(
                model_prob_range=(float(lo), float(hi)),
                factor=factor,
                sample_size=sample_size,
                actual_hit_rate=actual,
                model_avg_prob=model_avg,
            ))

            # Brier 计算
            for p, o in zip(bucket_probs, bucket_outcomes):
                brier_before += (p - o) ** 2
                cal_p = min(p * factor, 0.999)
                brier_after += (cal_p - o) ** 2

        n = len(observations)
        return CalibrationCurve(
            buckets=buckets,
            source="fitted",
            sample_size=n,
            brier_before=brier_before / n if n > 0 else 0,
            brier_after=brier_after / n if n > 0 else 0,
        )

    @staticmethod
    def fit_from_db(db_session=None) -> CalibrationCurve:
        """
        从数据库的历史比赛+预测数据拟合校准曲线。
        """
        if db_session is None:
            from models import SessionLocal
            db_session = SessionLocal()

        from models import Match, MatchStatus, Prediction

        matches = db_session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.closing_odds_home > 1.01,
        ).all()

        match_ids = [m.id for m in matches]
        preds = db_session.query(Prediction).filter(
            Prediction.match_id.in_(match_ids),
            Prediction.play_type == "SPF",
        ).all()

        pred_map = {p.match_id: p for p in preds}

        observations = []
        for m in matches:
            pred = pred_map.get(m.id)
            if not pred:
                continue
            probs = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)

            for sel in ["home", "draw", "away"]:
                model_p = probs.get(sel, 0)
                won = sel == m.actual_outcome
                observations.append((model_p, won))

        return Calibrator.fit_from_data(observations)

    @property
    def curve(self) -> CalibrationCurve:
        if self._curve is not None:
            return self._curve
        # 构建默认曲线
        buckets = [
            CalibrationBucket(
                model_prob_range=rng,
                factor=f,
                source="default",
            )
            for rng, f in DEFAULT_CALIBRATION_FACTORS.items()
        ]
        return CalibrationCurve(buckets=buckets, source="default", sample_size=15990)


def _normalize(probs: Dict[str, float], floor: float = 0.001) -> Dict[str, float]:
    """归一化概率: 确保 floor ≥ 每个值, 总和 = 1.0。"""
    # 应用 floor
    result = {k: max(v, floor) for k, v in probs.items()}
    total = sum(result.values())
    if total <= 0:
        # fallback: uniform
        n = len(result)
        return {k: 1.0 / n for k in result}
    # 归一化
    return {k: v / total for k, v in result.items()}
