"""
仓位计算器 — 校准版 Fractional Kelly + 回撤控制。

Kelly 公式给出理论最优仓位, 但有两个问题:
1. 输入概率不完美 → 用校准后概率 + 低分数 Kelly 缓解
2. 连亏时 Kelly 仍按总资金下注 → 用回撤控制减仓

四级风险档位:
- conservative (稳健): 1/8 Kelly, 上限 5%, DD>5% 减仓
- balanced (均衡): 1/4 Kelly, 上限 6%, DD>10% 减仓
- aggressive (进取): 1/4 Kelly, 上限 8%, DD>10% 减仓
- speculative (激进): 1/2 Kelly, 上限 10%, 无 DD 控制

用法:
    from position_sizer import PositionSizer, RiskTier
    sizer = PositionSizer(RiskTier.BALANCED)
    stake_pct = sizer.compute(calibrated_prob=0.55, odds=1.80,
                               bankroll=1000, peak=1050)
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Optional


class RiskTier(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    SPECULATIVE = "speculative"


# ─── 风险档位配置 ───
TIER_CONFIG: Dict[RiskTier, Dict] = {
    RiskTier.CONSERVATIVE: {
        "kelly_fraction": 0.125,     # 1/8 Kelly
        "max_stake_pct": 0.05,       # 硬上限 5%
        "dd_threshold_1": 0.05,      # DD>5% → ×0.75
        "dd_threshold_2": 0.10,      # DD>10% → ×0.50
        "dd_threshold_3": 0.20,      # DD>20% → ×0.25
        "dd_factor_1": 0.75,
        "dd_factor_2": 0.50,
        "dd_factor_3": 0.25,
    },
    RiskTier.BALANCED: {
        "kelly_fraction": 0.25,      # 1/4 Kelly
        "max_stake_pct": 0.06,
        "dd_threshold_1": 0.10,
        "dd_threshold_2": 0.15,
        "dd_threshold_3": 0.25,
        "dd_factor_1": 0.50,
        "dd_factor_2": 0.25,
        "dd_factor_3": 0.10,
    },
    RiskTier.AGGRESSIVE: {
        "kelly_fraction": 0.25,
        "max_stake_pct": 0.06,
        "dd_threshold_1": 0.08,
        "dd_threshold_2": 0.15,
        "dd_threshold_3": 0.25,
        "dd_factor_1": 0.50,
        "dd_factor_2": 0.25,
        "dd_factor_3": 0.10,
    },
    RiskTier.SPECULATIVE: {
        "kelly_fraction": 0.50,
        "max_stake_pct": 0.10,
        "dd_threshold_1": 1.0,       # 不触发
        "dd_threshold_2": 1.0,
        "dd_threshold_3": 1.0,
        "dd_factor_1": 1.0,
        "dd_factor_2": 1.0,
        "dd_factor_3": 1.0,
    },
}


@dataclass(frozen=True)
class StakeResult:
    """仓位计算结果"""
    stake_pct: float          # 最终仓位 (%)
    kelly_raw: float          # 原始 Kelly 比例
    kelly_adjusted: float     # 分数 Kelly
    dd_factor: float          # 回撤缩减因子
    stake_amount: float       # 仓位金额 (= bankroll × stake_pct)
    current_drawdown: float   # 当前回撤比例


def kelly_fraction(prob: float, odds: float) -> float:
    """
    标准 Kelly 公式: f = (p×o - 1) / (o - 1)

    返回值 ∈ [0, +∞)。负值表示无下注价值, 返回 0。
    """
    if odds <= 1.0 or prob <= 0:
        return 0.0
    k = (prob * odds - 1.0) / (odds - 1.0)
    return max(0.0, k)


class PositionSizer:
    """
    仓位计算器。

    用法:
        sizer = PositionSizer(RiskTier.BALANCED)
        result = sizer.compute(0.55, 1.80, bankroll=1000, peak=1050)
        print(result.stake_pct)  # 例如 0.032 (3.2%)
    """

    def __init__(self, tier: RiskTier = RiskTier.BALANCED):
        self._tier = tier
        self._cfg = TIER_CONFIG[tier]

    @property
    def tier(self) -> RiskTier:
        return self._tier

    def compute(
        self,
        calibrated_prob: float,
        odds: float,
        bankroll: float,
        peak: Optional[float] = None,
    ) -> StakeResult:
        """
        计算最终仓位。

        Args:
            calibrated_prob: 校准后概率
            odds: 赔率
            bankroll: 当前资金
            peak: 历史最高资金 (用于计算回撤)

        Returns:
            StakeResult with full computation details
        """
        if peak is None:
            peak = bankroll

        # 1. 原始 Kelly
        raw_k = kelly_fraction(calibrated_prob, odds)

        # 2. 分数 Kelly
        frac_k = raw_k * self._cfg["kelly_fraction"]

        # 3. 回撤控制
        dd = max(0.0, (peak - bankroll) / peak) if peak > 0 else 0.0
        dd_factor = 1.0
        if dd >= self._cfg["dd_threshold_3"]:
            dd_factor = self._cfg["dd_factor_3"]
        elif dd >= self._cfg["dd_threshold_2"]:
            dd_factor = self._cfg["dd_factor_2"]
        elif dd >= self._cfg["dd_threshold_1"]:
            dd_factor = self._cfg["dd_factor_1"]

        adjusted = frac_k * dd_factor

        # 4. 硬上限
        final_pct = min(adjusted, self._cfg["max_stake_pct"])

        # 5. 计算金额
        stake_amount = bankroll * final_pct

        return StakeResult(
            stake_pct=final_pct,
            kelly_raw=raw_k,
            kelly_adjusted=frac_k,
            dd_factor=dd_factor,
            stake_amount=stake_amount,
            current_drawdown=dd,
        )

    def compute_flat(
        self,
        bankroll: float,
        pct: float = 0.02,
    ) -> float:
        """固定比例仓位 (不依赖 Kelly)。"""
        return bankroll * min(pct, self._cfg["max_stake_pct"])
