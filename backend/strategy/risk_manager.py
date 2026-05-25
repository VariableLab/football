"""
风险管理器 — 组合暴露度、VaR、回撤监控。

管理跨多场比赛的风险, 避免过度集中:
- 单场暴露度上限
- 同联赛轮次暴露度上限
- 同联赛总暴露度上限
- VaR/CVaR 估算
- 最大回撤实时追踪

用法:
    from strategy.risk_manager import RiskManager, BetRecord
    rm = RiskManager(bankroll=1000)
    rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.04))
    rm.check(league="EPL", stake_pct=0.03)  # → True/False
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from math import log


@dataclass(frozen=True)
class BetRecord:
    """已下注记录"""
    match_id: int
    league: str
    round_key: str = ""     # 如 "EPL-2025-R15"
    stake_pct: float = 0.0  # 占资金比例
    calibrated_prob: float = 0.0
    odds: float = 0.0


@dataclass
class ExposureReport:
    """暴露度报告"""
    total_exposure: float
    single_match_max: float
    league_exposure: Dict[str, float]
    round_exposure: Dict[str, float]
    is_within_limits: bool
    violations: List[str] = field(default_factory=list)


@dataclass
class RiskAssessment:
    """完整风险评估"""
    risk_score: float       # 0-100
    risk_label: str         # low / medium / high / extreme
    var_95: float           # 95% VaR (占资金%)
    cvar_95: float          # 95% CVaR (占资金%)
    current_drawdown: float
    exposure: ExposureReport


# ─── 暴露度限制 ───
DEFAULT_LIMITS = {
    "single_match_max": 0.08,      # 单场 ≤ 8%
    "single_round_max": 0.15,      # 同轮次 ≤ 15%
    "single_league_max": 0.30,     # 同联赛 ≤ 30%
    "total_max": 0.60,             # 总暴露度 ≤ 60%
}


class RiskManager:
    """
    风险管理器。

    用法:
        rm = RiskManager(bankroll=1000)
        # 添加已有下注
        rm.add_bet(BetRecord(match_id=1, league="EPL", stake_pct=0.04))
        # 检查新下注是否允许
        ok = rm.check(league="EPL", round_key="EPL-R15", stake_pct=0.03)
    """

    def __init__(
        self,
        bankroll: float = 1000.0,
        peak: Optional[float] = None,
        limits: Optional[Dict] = None,
    ):
        self._bankroll = bankroll
        self._peak = peak or bankroll
        self._limits = limits or DEFAULT_LIMITS
        self._bets: List[BetRecord] = []
        self._bankroll_history: List[float] = [bankroll]

    @property
    def bankroll(self) -> float:
        return self._bankroll

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def current_drawdown(self) -> float:
        if self._peak <= 0:
            return 0.0
        return max(0.0, (self._peak - self._bankroll) / self._peak)

    def max_drawdown(self) -> float:
        """历史最大回撤。"""
        if not self._bankroll_history:
            return 0.0
        peak = self._bankroll_history[0]
        mdd = 0.0
        for br in self._bankroll_history:
            if br > peak:
                peak = br
            dd = (peak - br) / peak if peak > 0 else 0
            if dd > mdd:
                mdd = dd
        return mdd

    def update_bankroll(self, new_bankroll: float) -> None:
        """更新资金并记录历史。"""
        self._bankroll = new_bankroll
        if new_bankroll > self._peak:
            self._peak = new_bankroll
        self._bankroll_history.append(new_bankroll)

    def add_bet(self, bet: BetRecord) -> None:
        """记录一笔下注。"""
        self._bets.append(bet)

    def clear_bets(self) -> None:
        """清除所有下注记录 (新轮次开始时)。"""
        self._bets = []

    def exposure(self) -> ExposureReport:
        """计算当前暴露度。"""
        total = sum(b.stake_pct for b in self._bets)
        league_exp: Dict[str, float] = {}
        round_exp: Dict[str, float] = {}

        for b in self._bets:
            league_exp[b.league] = league_exp.get(b.league, 0) + b.stake_pct
            if b.round_key:
                round_exp[b.round_key] = round_exp.get(b.round_key, 0) + b.stake_pct

        single_max = max((b.stake_pct for b in self._bets), default=0)
        violations = []

        if single_max > self._limits["single_match_max"]:
            violations.append(f"单场暴露 {single_max:.1%} > {self._limits['single_match_max']:.1%}")
        for league, exp in league_exp.items():
            if exp > self._limits["single_league_max"]:
                violations.append(f"{league} 暴露 {exp:.1%} > {self._limits['single_league_max']:.1%}")
        for rnd, exp in round_exp.items():
            if exp > self._limits["single_round_max"]:
                violations.append(f"{rnd} 暴露 {exp:.1%} > {self._limits['single_round_max']:.1%}")
        if total > self._limits["total_max"]:
            violations.append(f"总暴露 {total:.1%} > {self._limits['total_max']:.1%}")

        return ExposureReport(
            total_exposure=total,
            single_match_max=single_max,
            league_exposure=league_exp,
            round_exposure=round_exp,
            is_within_limits=len(violations) == 0,
            violations=violations,
        )

    def check(
        self,
        league: str,
        round_key: str = "",
        stake_pct: float = 0.0,
    ) -> bool:
        """
        检查新下注是否在暴露度限制内。

        Returns:
            True if the bet is allowed, False otherwise
        """
        # 单场上限
        if stake_pct > self._limits["single_match_max"]:
            return False

        # 同轮次上限
        if round_key:
            round_total = sum(
                b.stake_pct for b in self._bets if b.round_key == round_key
            ) + stake_pct
            if round_total > self._limits["single_round_max"]:
                return False

        # 同联赛上限
        league_total = sum(
            b.stake_pct for b in self._bets if b.league == league
        ) + stake_pct
        if league_total > self._limits["single_league_max"]:
            return False

        # 总暴露度上限
        total = sum(b.stake_pct for b in self._bets) + stake_pct
        if total > self._limits["total_max"]:
            return False

        return True

    @staticmethod
    def compute_var(
        stake_pct: float,
        calibrated_prob: float,
        odds: float,
        confidence: float = 0.95,
    ) -> float:
        """
        计算 Value at Risk (VaR)。

        VaR_95 = 在 95% 置信度下的最大损失占比。
        简化模型: P(输) = 1 - calibrated_prob
        VaR_95 = stake_pct × P(输), 当 P(输) > 5% 时
        """
        p_loss = 1.0 - calibrated_prob
        if p_loss >= (1.0 - confidence):
            # 损失概率超过 (1-confidence), VaR = 全部注码
            return stake_pct
        else:
            # 损失概率低于阈值, VaR 接近 0
            return stake_pct * p_loss

    @staticmethod
    def compute_cvar(
        stake_pct: float,
        calibrated_prob: float,
        odds: float,
        confidence: float = 0.95,
    ) -> float:
        """
        计算 Conditional VaR (CVaR / Expected Shortfall)。

        CVaR = 当损失超过 VaR 时的平均损失。
        简化: CVaR ≈ stake_pct × (1 - calibrated_prob)²
        """
        p_loss = 1.0 - calibrated_prob
        return stake_pct * p_loss * p_loss

    @staticmethod
    def risk_score(
        calibrated_prob: float,
        odds: float,
        edge: float,
        overround_pct: float,
    ) -> float:
        """
        综合风险评分 (0-100, 越低越安全)。

        因子:
        - 概率越低 → 风险越高
        - 赔率越高 → 风险越高
        - 边际越小 → 风险越高
        - 返水率越高 → 风险越高
        """
        # 概率因子 (0-40): 低概率 → 高分
        prob_score = max(0, min(40, (1.0 - calibrated_prob) * 40))

        # 赔率因子 (0-25): 高赔率 → 高分
        odds_score = max(0, min(25, (odds - 1.0) * 8))

        # 边际因子 (0-20): 低边际 → 高分
        edge_score = max(0, min(20, max(0, 0.10 - edge) * 200))

        # 返水率因子 (0-15): 高返水 → 高分
        overround_score = max(0, min(15, overround_pct * 100 * 1.5))

        return min(100, prob_score + odds_score + edge_score + overround_score)

    @staticmethod
    def risk_label(score: float) -> str:
        """风险评分 → 标签。"""
        if score <= 25:
            return "low"
        elif score <= 50:
            return "medium"
        elif score <= 75:
            return "high"
        else:
            return "extreme"

    def assess(
        self,
        calibrated_prob: float,
        odds: float,
        edge: float,
        overround_pct: float,
        stake_pct: float,
    ) -> RiskAssessment:
        """生成完整风险评估。"""
        score = self.risk_score(calibrated_prob, odds, edge, overround_pct)
        var_95 = self.compute_var(stake_pct, calibrated_prob, odds)
        cvar_95 = self.compute_cvar(stake_pct, calibrated_prob, odds)

        return RiskAssessment(
            risk_score=round(score, 1),
            risk_label=self.risk_label(score),
            var_95=var_95,
            cvar_95=cvar_95,
            current_drawdown=self.current_drawdown,
            exposure=self.exposure(),
        )
