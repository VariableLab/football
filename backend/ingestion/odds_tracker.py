"""
赔率追踪器 — 开盘赔率识别 + 变化分析 + 蒸汽/尾盘检测。

从 OddsHistory 时序数据中提取开盘赔率、计算赔率漂移、
检测蒸汽盘（短时大幅变动）和尾盘资金（赛前最后变动）。

用法:
from odds_tracker import OddsTracker
tracker = OddsTracker(db_session)
report = tracker.analyze_match(match_id=42)
print(report.opening_odds, report.drift_home, report.steam_moves)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import and_

from logger import get_logger

logger = get_logger("odds_tracker")


# ─── 数据结构 ───

class MovementType(str, Enum):
    STEAM = "steam"         # 蒸汽盘：短时大幅变动（市场强烈信号）
    DRIFT = "drift"         # 渐进漂移：持续单向变动
    LATE_MONEY = "late_money"  # 尾盘资金：赛前 2h 内显著变动
    REVERSAL = "reversal"   # 反转：变动方向在短时间内逆转
    STABLE = "stable"       # 稳定：变动幅度 < 1%


@dataclass(frozen=True)
class OddsPoint:
    """赔率时间点"""
    recorded_at: datetime
    odds_home: float
    odds_draw: float
    odds_away: float
    source: str
    is_real: bool


@dataclass(frozen=True)
class OddsMovement:
    """单次赔率变动"""
    from_time: datetime
    to_time: datetime
    selection: str          # "home" / "draw" / "away"
    from_odds: float
    to_odds: float
    change_pct: float       # (to - from) / from
    movement_type: MovementType
    duration_minutes: float


@dataclass(frozen=True)
class SteamMove:
    """蒸汽盘信号"""
    selection: str
    from_odds: float
    to_odds: float
    change_pct: float
    window_minutes: float
    recorded_at: datetime
    direction: str          # "down" = 赔率下降（市场看好） / "up" = 赔率上升


@dataclass(frozen=True)
class MatchOddsReport:
    """单场比赛赔率分析报告"""
    match_id: int
    has_opening: bool
    opening_odds: Optional[OddsPoint]
    closing_odds: Optional[OddsPoint]
    current_odds: Optional[OddsPoint]
    drift_home_pct: Optional[float]   # 开盘→收盘漂移
    drift_draw_pct: Optional[float]
    drift_away_pct: Optional[float]
    steam_moves: Tuple[SteamMove, ...]
    late_money: Tuple[SteamMove, ...]
    movements: Tuple[OddsMovement, ...]
    total_snapshots: int
    real_snapshots: int
    signal: str              # "steam_down" / "late_money" / "drift" / "stable" / "no_data"


# ─── 阈值配置 ───

STEAM_THRESHOLD_PCT = 0.05       # 5% 短窗口变动 → 蒸汽盘
STEAM_MAX_WINDOW_MIN = 120       # 蒸汽盘最大窗口 2h
LATE_MONEY_THRESHOLD_PCT = 0.03  # 3% 尾盘变动
LATE_MONEY_WINDOW_HOURS = 2      # 尾盘窗口：赛前 2h
DRIFT_THRESHOLD_PCT = 0.02       # 2% 总漂移 → 有意义
REVERSAL_WINDOW_MIN = 60         # 反转检测窗口 1h
REVERSAL_MIN_PCT = 0.03          # 反转需要 3% 反向变动


# ─── 核心逻辑 ───

def _odds_change_pct(from_odds: float, to_odds: float) -> float:
    """计算赔率变动百分比 (to - from) / from。"""
    if from_odds <= 0:
        return 0.0
    return (to_odds - from_odds) / from_odds


def _point_diff(a: OddsPoint, b: OddsPoint, sel: str) -> float:
    """两个时间点之间某选项的赔率变动%。"""
    fa = getattr(a, f"odds_{sel}", 0)
    fb = getattr(b, f"odds_{sel}", 0)
    return _odds_change_pct(fa, fb)


class OddsTracker:
    """
    赔率追踪器。

    用法:
    tracker = OddsTracker(db_session)
    report = tracker.analyze_match(match_id=42)
    """

    def __init__(self, session: Session):
        self._session = session

    def analyze_match(self, match_id: int) -> MatchOddsReport:
        """
        分析单场比赛的赔率变化。

        从 OddsHistory 读取时序数据，识别开盘赔率、计算漂移、
        检测蒸汽盘和尾盘信号。
        """
        from models import OddsHistory

        snapshots = (
            self._session.query(OddsHistory)
            .filter(OddsHistory.match_id == match_id)
            .order_by(OddsHistory.recorded_at.asc())
            .all()
        )

        points = [
            OddsPoint(
                recorded_at=s.recorded_at,
                odds_home=s.odds_home,
                odds_draw=s.odds_draw,
                odds_away=s.odds_away,
                source=s.source,
                is_real=s.is_real,
            )
            for s in snapshots
        ]

        real_points = [p for p in points if p.is_real]

        # 识别开盘 / 收盘
        opening = real_points[0] if real_points else None
        closing = self._find_closing(real_points)

        # 计算漂移
        drift_h, drift_d, drift_a = self._compute_drift(opening, closing)

        # 检测变动
        movements = self._detect_movements(real_points)
        steam_moves = self._detect_steam(real_points)
        late_money = self._detect_late_money(real_points, match_id)

        # 综合信号
        signal = self._aggregate_signal(steam_moves, late_money, drift_h, drift_d, drift_a)

        return MatchOddsReport(
            match_id=match_id,
            has_opening=opening is not None,
            opening_odds=opening,
            closing_odds=closing,
            current_odds=real_points[-1] if real_points else None,
            drift_home_pct=drift_h,
            drift_draw_pct=drift_d,
            drift_away_pct=drift_a,
            steam_moves=tuple(steam_moves),
            late_money=tuple(late_money),
            movements=tuple(movements),
            total_snapshots=len(points),
            real_snapshots=len(real_points),
            signal=signal,
        )

    def update_opening_odds(self, match_id: int) -> bool:
        """
        将开盘赔率写回 Match 表。

        从 OddsHistory 找到最早的真实赔率快照，
        更新 Match.opening_odds_* 字段。

        Returns:
            True if opening odds were found and updated
        """
        from models import Match, OddsHistory

        earliest = (
            self._session.query(OddsHistory)
            .filter(and_(
                OddsHistory.match_id == match_id,
                OddsHistory.is_real.is_(True),
            ))
            .order_by(OddsHistory.recorded_at.asc())
            .first()
        )

        if earliest is None:
            return False

        match = self._session.query(Match).filter(Match.id == match_id).first()
        if match is None:
            return False

        match.opening_odds_home = earliest.odds_home
        match.opening_odds_draw = earliest.odds_draw
        match.opening_odds_away = earliest.odds_away
        match.opening_odds_source = earliest.source
        match.opening_odds_at = earliest.recorded_at

        self._session.commit()
        logger.info(
            f"Match {match_id}: opening odds set from {earliest.source} "
            f"@ {earliest.recorded_at}: "
            f"H={earliest.odds_home:.2f} D={earliest.odds_draw:.2f} A={earliest.odds_away:.2f}"
        )
        return True

    def batch_update_opening_odds(self) -> int:
        """批量更新所有缺少开盘赔率的比赛。返回更新数量。"""
        from models import Match

        matches = (
            self._session.query(Match)
            .filter(Match.opening_odds_home.is_(None))
            .all()
        )

        updated = 0
        for m in matches:
            if self.update_opening_odds(m.id):
                updated += 1

        logger.info(f"Batch opening odds update: {updated}/{len(matches)} matches updated")
        return updated

    def scan_steam_moves(self, competition: str = "") -> List[Tuple[int, SteamMove]]:
        """
        扫描所有近期比赛的蒸汽盘信号。

        Returns:
            List of (match_id, steam_move) tuples
        """
        from models import Match, OddsHistory

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        query = self._session.query(Match).filter(Match.kickoff_at >= cutoff)
        if competition:
            query = query.filter(Match.competition == competition)

        matches = query.all()
        results: List[Tuple[int, SteamMove]] = []

        for m in matches:
            report = self.analyze_match(m.id)
            for steam in report.steam_moves:
                results.append((m.id, steam))

        return results

    # ─── 内部方法 ───

    @staticmethod
    def _find_closing(points: List[OddsPoint]) -> Optional[OddsPoint]:
        """找到收盘赔率（最后一个 is_closing 或最后一个真实快照）。"""
        if not points:
            return None
        # 优先找标记为 closing 的
        return points[-1]

    @staticmethod
    def _compute_drift(
        opening: Optional[OddsPoint],
        closing: Optional[OddsPoint],
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """计算开盘→收盘漂移百分比。"""
        if opening is None or closing is None:
            return None, None, None
        h = _odds_change_pct(opening.odds_home, closing.odds_home)
        d = _odds_change_pct(opening.odds_draw, closing.odds_draw)
        a = _odds_change_pct(opening.odds_away, closing.odds_away)
        return h, d, a

    @staticmethod
    def _detect_movements(points: List[OddsPoint]) -> List[OddsMovement]:
        """检测所有赔率变动事件。"""
        if len(points) < 2:
            return []

        movements: List[OddsMovement] = []

        for i in range(1, len(points)):
            prev = points[i - 1]
            curr = points[i]
            dur = (curr.recorded_at - prev.recorded_at).total_seconds() / 60

            for sel in ("home", "draw", "away"):
                prev_o = getattr(prev, f"odds_{sel}")
                curr_o = getattr(curr, f"odds_{sel}")
                chg = _odds_change_pct(prev_o, curr_o)

                if abs(chg) < 0.005:
                    continue  # <0.5% 忽略

                mtype = MovementType.STABLE
                if abs(chg) >= STEAM_THRESHOLD_PCT and dur <= STEAM_MAX_WINDOW_MIN:
                    mtype = MovementType.STEAM
                elif abs(chg) >= DRIFT_THRESHOLD_PCT:
                    mtype = MovementType.DRIFT

                movements.append(OddsMovement(
                    from_time=prev.recorded_at,
                    to_time=curr.recorded_at,
                    selection=sel,
                    from_odds=prev_o,
                    to_odds=curr_o,
                    change_pct=chg,
                    movement_type=mtype,
                    duration_minutes=dur,
                ))

        # 检测反转
        if len(movements) >= 2:
            for i in range(1, len(movements)):
                prev_m = movements[i - 1]
                curr_m = movements[i]
                if (prev_m.selection == curr_m.selection
                        and prev_m.change_pct * curr_m.change_pct < 0
                        and abs(curr_m.change_pct) >= REVERSAL_MIN_PCT):
                    gap_min = (curr_m.from_time - prev_m.to_time).total_seconds() / 60
                    if gap_min <= REVERSAL_WINDOW_MIN:
                        movements.append(OddsMovement(
                            from_time=prev_m.to_time,
                            to_time=curr_m.to_time,
                            selection=curr_m.selection,
                            from_odds=prev_m.to_odds,
                            to_odds=curr_m.to_odds,
                            change_pct=curr_m.change_pct - prev_m.change_pct,
                            movement_type=MovementType.REVERSAL,
                            duration_minutes=gap_min,
                        ))

        return movements

    @staticmethod
    def _detect_steam(points: List[OddsPoint]) -> List[SteamMove]:
        """检测蒸汽盘：滑动窗口 + 逐选项最大变动。O(n) per selection。"""
        if len(points) < 2:
            return []

        steam: List[SteamMove] = []

        for sel in ("home", "draw", "away"):
            # 对每个选项，用滑动窗口找最大变动
            left = 0
            for right in range(1, len(points)):
                # 推进左指针，保持窗口 <= STEAM_MAX_WINDOW_MIN
                while left < right:
                    gap = (points[right].recorded_at - points[left].recorded_at).total_seconds() / 60
                    if gap <= STEAM_MAX_WINDOW_MIN:
                        break
                    left += 1

                if left >= right:
                    continue

                o_l = getattr(points[left], f"odds_{sel}")
                o_r = getattr(points[right], f"odds_{sel}")
                chg = _odds_change_pct(o_l, o_r)

                if abs(chg) >= STEAM_THRESHOLD_PCT:
                    # 检查是否已报告过此选项的相近信号
                    already = any(
                        s.selection == sel
                        and abs(s.recorded_at - points[right].recorded_at).total_seconds() < 300
                        for s in steam
                    )
                    if not already:
                        gap = (points[right].recorded_at - points[left].recorded_at).total_seconds() / 60
                        direction = "down" if chg < 0 else "up"
                        steam.append(SteamMove(
                            selection=sel,
                            from_odds=o_l,
                            to_odds=o_r,
                            change_pct=chg,
                            window_minutes=gap,
                            recorded_at=points[right].recorded_at,
                            direction=direction,
                        ))

        return steam

    def _detect_late_money(
        self, points: List[OddsPoint], match_id: int,
    ) -> List[SteamMove]:
        """检测尾盘资金：赛前 2h 内的显著变动。"""
        from models import Match

        match = self._session.query(Match).filter(Match.id == match_id).first()
        if match is None or match.kickoff_at is None:
            return []

        kickoff = match.kickoff_at
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        window_start = kickoff - timedelta(hours=LATE_MONEY_WINDOW_HOURS)

        # 筛选窗口内的真实快照（统一时区处理）
        def _ensure_tz(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        window_points = [
            p for p in points
            if _ensure_tz(p.recorded_at) >= window_start and _ensure_tz(p.recorded_at) <= kickoff
        ]

        if len(window_points) < 2:
            return []

        late: List[SteamMove] = []

        # 使用首尾对比代替 O(n^2) 配对
        if len(window_points) >= 2:
            first = window_points[0]
            last = window_points[-1]

            for sel in ("home", "draw", "away"):
                o_first = getattr(first, f"odds_{sel}")
                o_last = getattr(last, f"odds_{sel}")
                chg = _odds_change_pct(o_first, o_last)

                if abs(chg) >= LATE_MONEY_THRESHOLD_PCT:
                    direction = "down" if chg < 0 else "up"
                    gap = (last.recorded_at - first.recorded_at).total_seconds() / 60
                    late.append(SteamMove(
                        selection=sel,
                        from_odds=o_first,
                        to_odds=o_last,
                        change_pct=chg,
                        window_minutes=gap,
                        recorded_at=last.recorded_at,
                        direction=direction,
                    ))

        return late

    @staticmethod
    def _aggregate_signal(
        steam: List[SteamMove],
        late: List[SteamMove],
        drift_h: Optional[float],
        drift_d: Optional[float],
        drift_a: Optional[float],
    ) -> str:
        """综合信号判断。"""
        if steam:
            # 蒸汽盘信号最强
            down_steam = [s for s in steam if s.direction == "down"]
            if down_steam:
                return "steam_down"
            return "steam_up"

        if late:
            return "late_money"

        # 检查漂移
        drifts = [d for d in (drift_h, drift_d, drift_a) if d is not None]
        if drifts and any(abs(d) >= DRIFT_THRESHOLD_PCT for d in drifts):
            return "drift"

        return "stable"
