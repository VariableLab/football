"""
滚球对冲引擎 — 实时赔率监控 + 自动对冲机会检测。

基于 LiveOddsFeed 推送的实时赔率变动，自动检测:
1. 滚球套利: 滚球赔率与赛前赔率的套利窗口
2. 对冲时机: 领先/落后时最优对冲赔率点
3. 止损对冲: 赔率大幅不利变动时的紧急对冲
4. 部分锁定: 比赛进行中部分锁定利润

用法:
from live_hedge_engine import LiveHedgeEngine, HedgeAlert
from live_odds_feed import get_odds_bus

engine = LiveHedgeEngine(db_session, bus=get_odds_bus())
engine.start_monitoring()

# 或手动检查
alerts = engine.check_hedge_opportunities(match_id=42)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

from hedge_engine import HedgeEngine, HedgeResult, compute_implied_total
from live_odds_feed import OddsBus, LiveOddsUpdate, get_odds_bus
from utils.logger import get_logger

logger = get_logger("live_hedge_engine")


# ─── 数据结构 ───

class AlertLevel(str, Enum):
    INFO = "info"           # 信息性通知
    OPPORTUNITY = "opportunity"  # 套利/对冲机会
    URGENT = "urgent"       # 紧急对冲（止损级别）
    CRITICAL = "critical"   # 严重风险（需立即行动）


class HedgeType(str, Enum):
    ARBITRAGE = "arbitrage"       # 滚球套利
    LOCK_PROFIT = "lock_profit"   # 锁定利润
    STOP_LOSS = "stop_loss"       # 止损对冲
    PARTIAL_HEDGE = "partial_hedge"  # 部分对冲


@dataclass(frozen=True)
class HedgeAlert:
    """对冲警报"""
    match_id: int
    alert_level: AlertLevel
    hedge_type: HedgeType
    message: str
    current_odds: Dict[str, float]          # 当前滚球赔率
    pre_match_odds: Dict[str, float]        # 赛前赔率
    hedge_result: Optional[HedgeResult]     # 对冲计算结果
    implied_total: float                    # 隐含概率总和
    profit_pct: float                       # 利润率
    timestamp: datetime


@dataclass
class Position:
    """已有仓位"""
    match_id: int
    selection: str          # "home" / "draw" / "away"
    odds: float             # 买入赔率
    stake: float            # 投注金额
    placed_at: datetime


@dataclass
class HedgeWatchConfig:
    """监控配置"""
    arb_profit_threshold: float = 0.01       # 套利利润阈值 1%
    stop_loss_odds_change: float = 0.15      # 止损赔率变动 15%
    lock_profit_min_edge: float = 0.03       # 锁定利润最小边际 3%
    partial_hedge_fraction: float = 0.5      # 部分对冲比例
    min_match_minute_for_hedge: int = 30     # 最早对冲时间(分钟)


# ─── 滚球对冲引擎 ───

class LiveHedgeEngine:
    """
    滚球对冲引擎。

    监控 OddsBus 推送的实时赔率，检测对冲机会。

    用法:
    engine = LiveHedgeEngine(db_session, bus=get_odds_bus())
    engine.start_monitoring()
    # 或
    alerts = engine.check_hedge_opportunities(match_id=42)
    """

    def __init__(
        self,
        session: Optional[Session] = None,
        bus: Optional[OddsBus] = None,
        config: Optional[HedgeWatchConfig] = None,
    ):
        self._bus = bus or get_odds_bus()
        self._config = config or HedgeWatchConfig()
        self._positions: Dict[int, Position] = {}
        self._alerts: List[HedgeAlert] = []
        self._max_alerts = 100
        self._subscriber = None

    @property
    def positions(self) -> Dict[int, Position]:
        return dict(self._positions)

    @property
    def recent_alerts(self) -> List[HedgeAlert]:
        return list(self._alerts[-20:])

    def add_position(self, position: Position) -> None:
        """添加已有仓位。"""
        self._positions[position.match_id] = position

    def remove_position(self, match_id: int) -> None:
        """移除仓位（对冲完成或比赛结束）。"""
        self._positions.pop(match_id, None)

    def start_monitoring(self) -> None:
        """启动实时监控（订阅 OddsBus）。"""
        self._subscriber = self._bus.subscribe(
            callback=self._on_odds_update,
            min_change_pct=0.01,  # 1% 以上变动才触发
        )
        logger.info("LiveHedgeEngine monitoring started")

    def stop_monitoring(self) -> None:
        """停止实时监控。"""
        if self._subscriber:
            self._bus.unsubscribe(self._subscriber)
            self._subscriber = None
        logger.info("LiveHedgeEngine monitoring stopped")

    def _on_odds_update(self, update: LiveOddsUpdate) -> None:
        """OddsBus 回调：实时赔率更新时自动检查。"""
        alerts = self.check_hedge_opportunities(update.match_id)
        for alert in alerts:
            self._add_alert(alert)
            if alert.alert_level in (AlertLevel.URGENT, AlertLevel.CRITICAL):
                logger.warning(
                    f"[HEDGE {alert.alert_level.value}] Match {alert.match_id}: "
                    f"{alert.message}"
                )

    def check_hedge_opportunities(self, match_id: int) -> List[HedgeAlert]:
        """
        检查单场比赛的对冲机会。

        检查三类:
        1. 滚球套利: 当前赔率是否形成套利
        2. 锁定利润: 已有仓位是否可以锁定利润
        3. 止损: 已有仓位的赔率是否大幅不利变动
        """
        alerts: List[HedgeAlert] = []
        now = datetime.now(timezone.utc)

        # 获取最新滚球赔率
        latest = self._bus.get_latest(match_id)
        if latest is None:
            return alerts

        # 获取赛前赔率
        from database.models import Match, SessionLocal
        session = SessionLocal()
        try:
            match = session.query(Match).filter(Match.id == match_id).first()
        finally:
            session.close()
        if not match:
            return alerts

        pre_match = {
            "home": match.closing_odds_home or match.odds_home or 2.0,
            "draw": match.closing_odds_draw or match.odds_draw or 3.2,
            "away": match.closing_odds_away or match.odds_away or 3.5,
        }

        current = {
            "home": latest.odds_home,
            "draw": latest.odds_draw,
            "away": latest.odds_away,
        }

        # 1. 滚球套利检查
        arb_alert = self._check_arbitrage(match_id, current, now)
        if arb_alert:
            alerts.append(arb_alert)

        # 2. 已有仓位的对冲检查
        position = self._positions.get(match_id)
        if position:
            # 锁定利润
            lock_alert = self._check_lock_profit(
                match_id, position, current, pre_match, now
            )
            if lock_alert:
                alerts.append(lock_alert)

            # 止损检查
            stop_alert = self._check_stop_loss(
                match_id, position, latest, now
            )
            if stop_alert:
                alerts.append(stop_alert)

        return alerts

    def scan_all_opportunities(self) -> List[HedgeAlert]:
        """扫描所有有滚球赔率的比赛。"""
        all_latest = self._bus.get_all_latest()
        alerts: List[HedgeAlert] = []

        for mid in all_latest:
            match_alerts = self.check_hedge_opportunities(mid)
            alerts.extend(match_alerts)

        alerts.sort(key=lambda a: (
            0 if a.alert_level == AlertLevel.CRITICAL
            else 1 if a.alert_level == AlertLevel.URGENT
            else 2 if a.alert_level == AlertLevel.OPPORTUNITY
            else 3
        ))

        return alerts

    # ─── 内部检查方法 ───

    def _check_arbitrage(
        self,
        match_id: int,
        current_odds: Dict[str, float],
        now: datetime,
    ) -> Optional[HedgeAlert]:
        """检查滚球套利机会。"""
        implied = compute_implied_total(
            current_odds["home"],
            current_odds["draw"],
            current_odds["away"],
        )

        if implied >= 1.0:
            return None

        profit_pct = 1.0 / implied - 1.0
        if profit_pct < self._config.arb_profit_threshold:
            return None

        # 扣除交易成本后
        net_profit = profit_pct - 0.03  # 3% 交易成本

        level = AlertLevel.OPPORTUNITY
        if net_profit > 0.05:
            level = AlertLevel.URGENT

        return HedgeAlert(
            match_id=match_id,
            alert_level=level,
            hedge_type=HedgeType.ARBITRAGE,
            message=f"滚球套利: 利润率{profit_pct:.1%} (净{net_profit:+.1%})",
            current_odds=current_odds,
            pre_match_odds={},
            hedge_result=None,
            implied_total=implied,
            profit_pct=net_profit,
            timestamp=now,
        )

    def _check_lock_profit(
        self,
        match_id: int,
        position: Position,
        current_odds: Dict[str, float],
        pre_match_odds: Dict[str, float],
        now: datetime,
    ) -> Optional[HedgeAlert]:
        """检查是否可以锁定利润。"""
        # 找对冲选项（非原选项的另一侧）
        hedge_selections = [s for s in ["home", "draw", "away"] if s != position.selection]
        if not hedge_selections:
            return None

        best_hedge = None
        for sel in hedge_selections:
            hedge_odds = current_odds.get(sel, 0)
            if hedge_odds <= 1.0:
                continue

            result = HedgeEngine.compute_hedge(
                original_odds=position.odds,
                original_stake=position.stake,
                hedge_odds=hedge_odds,
            )

            if result.is_profitable:
                if best_hedge is None or result.guaranteed_profit > best_hedge.guaranteed_profit:
                    best_hedge = (sel, hedge_odds, result)

        if best_hedge is None:
            return None

        sel, odds, result = best_hedge

        # 检查边际是否足够
        edge = result.guaranteed_profit / (position.stake + result.hedge_stake)
        if edge < self._config.lock_profit_min_edge:
            return None

        # 检查比赛时间是否足够早（太晚没意义）
        latest = self._bus.get_latest(match_id)
        if latest and latest.match_minute is not None:
            if latest.match_minute < self._config.min_match_minute_for_hedge:
                return None

        return HedgeAlert(
            match_id=match_id,
            alert_level=AlertLevel.OPPORTUNITY,
            hedge_type=HedgeType.LOCK_PROFIT,
            message=f"锁定利润: 对冲{sel}@{odds:.2f}, 保底赚¥{result.guaranteed_profit:.1f}",
            current_odds=current_odds,
            pre_match_odds=pre_match_odds,
            hedge_result=result,
            implied_total=0,
            profit_pct=edge,
            timestamp=now,
        )

    def _check_stop_loss(
        self,
        match_id: int,
        position: Position,
        update: LiveOddsUpdate,
        now: datetime,
    ) -> Optional[HedgeAlert]:
        """检查是否需要止损对冲。"""
        # 检查原选项赔率是否大幅上升（不利方向）
        current_odds = {
            "home": update.odds_home,
            "draw": update.odds_draw,
            "away": update.odds_away,
        }

        sel_odds = current_odds.get(position.selection, 0)
        if sel_odds <= 0:
            return None

        change_pct = (sel_odds - position.odds) / position.odds if position.odds > 0 else 0

        # 原选项赔率上升超过阈值 → 不利信号
        if position.selection == "home":
            adverse = update.home_change_pct
        elif position.selection == "draw":
            adverse = update.draw_change_pct
        else:
            adverse = update.away_change_pct

        if adverse < self._config.stop_loss_odds_change:
            # 紧急对冲
            hedge_sels = [s for s in ["home", "draw", "away"] if s != position.selection]
            hedge_odds = max(current_odds.get(s, 0) for s in hedge_sels)

            result = None
            if hedge_odds > 1.0:
                result = HedgeEngine.compute_partial_hedge(
                    position.odds, position.stake, hedge_odds,
                    self._config.partial_hedge_fraction,
                )

            return HedgeAlert(
                match_id=match_id,
                alert_level=AlertLevel.URGENT,
                hedge_type=HedgeType.STOP_LOSS,
                message=f"止损警告: {position.selection}赔率变动{adverse:+.1%}, 建议对冲{self._config.partial_hedge_fraction:.0%}仓位",
                current_odds=current_odds,
                pre_match_odds={},
                hedge_result=result,
                implied_total=0,
                profit_pct=0,
                timestamp=now,
            )

        return None

    def _add_alert(self, alert: HedgeAlert) -> None:
        """添加警报。"""
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

    def compute_live_hedge(
        self,
        match_id: int,
        original_selection: str,
        original_odds: float,
        original_stake: float,
        hedge_fraction: float = 1.0,
    ) -> Optional[HedgeResult]:
        """
        计算滚球对冲方案。

        基于当前滚球赔率，计算对冲投注额。
        """
        latest = self._bus.get_latest(match_id)
        if latest is None:
            return None

        current_odds = {
            "home": latest.odds_home,
            "draw": latest.odds_draw,
            "away": latest.odds_away,
        }

        hedge_sels = [s for s in ["home", "draw", "away"] if s != original_selection]
        best_hedge_odds = max(current_odds.get(s, 0) for s in hedge_sels)

        if best_hedge_odds <= 1.0:
            return None

        if hedge_fraction >= 1.0:
            return HedgeEngine.compute_hedge(
                original_odds, original_stake, best_hedge_odds
            )
        else:
            return HedgeEngine.compute_partial_hedge(
                original_odds, original_stake, best_hedge_odds, hedge_fraction
            )
