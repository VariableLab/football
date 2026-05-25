"""
滚球赔率流 — 实时赔率采集 + 内存总线 + SSE推送。

架构:
1. LiveOddsFeed: 可配置的赔率采集引擎，支持多数据源
   - OddsApiLiveSource: the-odds-api.com 滚球赔率 (需 API key)
   - BetExplorerLiveSource: BetExplorer 滚球页面爬虫
   - JingcaiLiveSource: 竞彩官网实时赔率
   - SimulatedLiveSource: 基于赛前赔率的模拟滚球 (无需外部API)

2. OddsBus: 内存发布/订阅总线，采集→推送零延迟

3. SSE endpoint: FastAPI 推送实时赔率到前端

用法:
from live_odds_feed import LiveOddsFeed, OddsBus

# 启动采集
feed = LiveOddsFeed(db_session)
feed.start()

# 订阅实时赔率
bus = OddsBus()
bus.subscribe("match_42", callback)

# SSE endpoint
@app.get("/api/live-odds/stream")
async def live_odds_sse(request: Request):
    ...
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import and_

from logger import get_logger

logger = get_logger("live_odds_feed")


# ─── 数据结构 ───

@dataclass(frozen=True)
class LiveOddsUpdate:
    """实时赔率更新"""
    match_id: int
    source: str
    odds_home: float
    odds_draw: float
    odds_away: float
    match_minute: Optional[int]
    live_score_home: Optional[int]
    live_score_away: Optional[int]
    timestamp: datetime
    # 变动检测
    prev_odds_home: Optional[float] = None
    prev_odds_draw: Optional[float] = None
    prev_odds_away: Optional[float] = None
    home_change_pct: float = 0.0
    draw_change_pct: float = 0.0
    away_change_pct: float = 0.0


@dataclass
class Subscriber:
    """订阅者"""
    callback: Callable[[LiveOddsUpdate], None]
    match_id: Optional[int] = None  # None = 订阅全部
    min_change_pct: float = 0.005  # 最小变动阈值 0.5%


# ─── 内存发布/订阅总线 ───

class OddsBus:
    """
    内存赔率总线。采集线程写入，SSE/回调读取。

    用法:
    bus = OddsBus()
    bus.subscribe(callback, match_id=42)
    bus.publish(update)
    """

    def __init__(self):
        self._subscribers: List[Subscriber] = []
        self._lock = threading.Lock()
        self._latest: Dict[int, LiveOddsUpdate] = {}
        self._history: Dict[int, List[LiveOddsUpdate]] = defaultdict(list)
        self._max_history = 100  # 每场最多保留100条

    def subscribe(
        self,
        callback: Callable[[LiveOddsUpdate], None],
        match_id: Optional[int] = None,
        min_change_pct: float = 0.005,
    ) -> Subscriber:
        """订阅实时赔率更新。"""
        sub = Subscriber(
            callback=callback,
            match_id=match_id,
            min_change_pct=min_change_pct,
        )
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """取消订阅。"""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not sub]

    def publish(self, update: LiveOddsUpdate) -> None:
        """发布赔率更新。"""
        # 计算变动
        prev = self._latest.get(update.match_id)
        if prev:
            update = LiveOddsUpdate(
                match_id=update.match_id,
                source=update.source,
                odds_home=update.odds_home,
                odds_draw=update.odds_draw,
                odds_away=update.odds_away,
                match_minute=update.match_minute,
                live_score_home=update.live_score_home,
                live_score_away=update.live_score_away,
                timestamp=update.timestamp,
                prev_odds_home=prev.odds_home,
                prev_odds_draw=prev.odds_draw,
                prev_odds_away=prev.odds_away,
                home_change_pct=_change_pct(prev.odds_home, update.odds_home),
                draw_change_pct=_change_pct(prev.odds_draw, update.odds_draw),
                away_change_pct=_change_pct(prev.odds_away, update.odds_away),
            )

        with self._lock:
            self._latest[update.match_id] = update
            history = self._history[update.match_id]
            history.append(update)
            if len(history) > self._max_history:
                self._history[update.match_id] = history[-self._max_history:]
            subs = list(self._subscribers)

        # 通知订阅者（在锁外，避免死锁）
        for sub in subs:
            if sub.match_id is not None and sub.match_id != update.match_id:
                continue
            # 检查变动是否超过阈值
            max_change = max(
                abs(update.home_change_pct),
                abs(update.draw_change_pct),
                abs(update.away_change_pct),
            )
            if max_change < sub.min_change_pct and prev is not None:
                continue
            try:
                sub.callback(update)
            except Exception as e:
                logger.error(f"Subscriber callback error: {e}")

    def get_latest(self, match_id: int) -> Optional[LiveOddsUpdate]:
        """获取某场比赛的最新赔率。"""
        with self._lock:
            return self._latest.get(match_id)

    def get_all_latest(self) -> Dict[int, LiveOddsUpdate]:
        """获取所有比赛的最新赔率。"""
        with self._lock:
            return dict(self._latest)

    def get_history(self, match_id: int, limit: int = 20) -> List[LiveOddsUpdate]:
        """获取某场比赛的赔率历史。"""
        with self._lock:
            history = self._history.get(match_id, [])
            return list(history[-limit:])


# 全局单例
_odds_bus: Optional[OddsBus] = None


def get_odds_bus() -> OddsBus:
    """获取全局赔率总线。"""
    global _odds_bus
    if _odds_bus is None:
        _odds_bus = OddsBus()
    return _odds_bus


# ─── 数据源抽象 ───

class LiveOddsSource(ABC):
    """滚球赔率数据源抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def fetch_live_odds(
        self,
        match_ids: List[int],
        session: Session,
    ) -> List[LiveOddsUpdate]:
        """获取指定比赛的实时赔率。"""
        ...


class SimulatedLiveSource(LiveOddsSource):
    """
    模拟滚球赔率源（无需外部API）。

    基于赛前赔率，根据比赛进程模拟赔率变动:
    - 进球: 进球方赔率大幅下降
    - 时间推进: 领先方赔率逐渐下降
    - 随机波动: ±0.5-2% 的小幅变动
    """

    def __init__(self):
        self._sim_state: Dict[int, Dict] = {}

    @property
    def name(self) -> str:
        return "simulated_live"

    def fetch_live_odds(
        self,
        match_ids: List[int],
        session: Session,
    ) -> List[LiveOddsUpdate]:
        from models import Match

        updates: List[LiveOddsUpdate] = []
        now = datetime.now(timezone.utc)

        for mid in match_ids:
            match = session.query(Match).filter(Match.id == mid).first()
            if not match or match.status.value not in ("live", "scheduled", "upcoming"):
                continue

            base_h = match.odds_home or 2.0
            base_d = match.odds_draw or 3.2
            base_a = match.odds_away or 3.5

            state = self._sim_state.get(mid, {
                "minute": 0,
                "score_h": 0,
                "score_a": 0,
                "odds_h": base_h,
                "odds_d": base_d,
                "odds_a": base_a,
                "last_update": now,
            })

            # 模拟时间推进
            elapsed = (now - state["last_update"]).total_seconds() / 60
            state["minute"] = min(90, state["minute"] + max(1, int(elapsed)))
            state["last_update"] = now

            # 模拟赔率变动（基于比分和时间的随机游走）
            import random
            noise = random.gauss(0, 0.01)  # ±1% 标准差

            # 比分领先方赔率随时间递减
            if state["score_h"] > state["score_a"]:
                time_factor = state["minute"] / 90.0
                state["odds_h"] = max(1.01, base_h * (1 - 0.3 * time_factor) + noise)
                state["odds_d"] = base_d * (1 + 0.2 * time_factor) + noise
                state["odds_a"] = base_a * (1 + 0.3 * time_factor) + noise
            elif state["score_a"] > state["score_h"]:
                time_factor = state["minute"] / 90.0
                state["odds_h"] = base_h * (1 + 0.3 * time_factor) + noise
                state["odds_d"] = base_d * (1 + 0.2 * time_factor) + noise
                state["odds_a"] = max(1.01, base_a * (1 - 0.3 * time_factor) + noise)
            else:
                # 平局：赔率缓慢向平局方向移动
                time_factor = state["minute"] / 90.0
                state["odds_h"] = base_h * (1 + 0.05 * time_factor) + noise
                state["odds_d"] = max(1.01, base_d * (1 - 0.1 * time_factor) + noise)
                state["odds_a"] = base_a * (1 + 0.05 * time_factor) + noise

            # 确保赔率合理
            state["odds_h"] = max(1.01, round(state["odds_h"], 2))
            state["odds_d"] = max(1.01, round(state["odds_d"], 2))
            state["odds_a"] = max(1.01, round(state["odds_a"], 2))

            self._sim_state[mid] = state

            updates.append(LiveOddsUpdate(
                match_id=mid,
                source=self.name,
                odds_home=state["odds_h"],
                odds_draw=state["odds_d"],
                odds_away=state["odds_a"],
                match_minute=state["minute"],
                live_score_home=state["score_h"],
                live_score_away=state["score_a"],
                timestamp=now,
            ))

        return updates


class OddsApiLiveSource(LiveOddsSource):
    """
    The Odds API 滚球赔率源。

    文档: https://the-odds-api.com/liveapi/
    需要 ODDS_API_KEY 和付费套餐。
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._base_url = "https://api.the-odds-api.com/v4/sports"

    @property
    def name(self) -> str:
        return "oddsapi_live"

    def fetch_live_odds(
        self,
        match_ids: List[int],
        session: Session,
    ) -> List[LiveOddsUpdate]:
        if not self._api_key:
            return []

        try:
            import httpx
        except ImportError:
            return []

        from models import Match

        updates: List[LiveOddsUpdate] = []
        now = datetime.now(timezone.utc)

        # 获取比赛信息用于 team name 匹配
        matches = session.query(Match).filter(Match.id.in_(match_ids)).all()
        if not matches:
            return []

        # 构建查找表: team_name → match_id
        team_to_match: Dict[str, int] = {}
        for m in matches:
            if m.home_team:
                team_to_match[m.home_team.name.lower()] = m.id
            if m.away_team:
                team_to_match[m.away_team.name.lower()] = m.id

        # 调用 API — 获取足球滚球赔率
        try:
            client = httpx.Client(timeout=10.0)
            for sport in ["soccer_epl", "soccer_germany_bundesliga",
                          "soccer_italy_serie_a", "soccer_spain_la_liga",
                          "soccer_fifa_world_cup"]:
                url = f"{self._base_url}/{sport}/odds"
                params = {
                    "api_key": self._api_key,
                    "regions": "eu",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                }

                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    continue

                events = resp.json()
                for event in events:
                    home = event.get("home_team", "").lower()
                    away = event.get("away_team", "").lower()

                    mid = team_to_match.get(home) or team_to_match.get(away)
                    if mid is None:
                        continue

                    # 提取最优赔率
                    best_h = best_d = best_a = 0.0
                    for bk in event.get("bookmakers", []):
                        for market in bk.get("markets", []):
                            if market.get("key") != "h2h":
                                continue
                            for outcome in market.get("outcomes", []):
                                name = outcome.get("name", "").lower()
                                price = outcome.get("price", 0)
                                if name == home and price > best_h:
                                    best_h = price
                                elif name == "draw" and price > best_d:
                                    best_d = price
                                elif name == away and price > best_a:
                                    best_a = price

                    if best_h > 0 and best_d > 0 and best_a > 0:
                        updates.append(LiveOddsUpdate(
                            match_id=mid,
                            source=self.name,
                            odds_home=best_h,
                            odds_draw=best_d,
                            odds_away=best_a,
                            match_minute=None,
                            live_score_home=None,
                            live_score_away=None,
                            timestamp=now,
                        ))

            client.close()
        except Exception as e:
            logger.error(f"OddsApiLiveSource fetch error: {e}")

        return updates


class JingcaiLiveSource(LiveOddsSource):
    """
    竞彩官网实时赔率源。

    从 sporttery.cn 获取竞彩在售比赛的最新赔率。
    无需 API key，但有频率限制。
    """

    def __init__(self):
        self._cache: Dict[int, LiveOddsUpdate] = {}
        self._cache_ttl = 30  # 缓存30秒

    @property
    def name(self) -> str:
        return "jingcai_live"

    def fetch_live_odds(
        self,
        match_ids: List[int],
        session: Session,
    ) -> List[LiveOddsUpdate]:
        from models import Match, JingcaiIssue, JingcaiIssueMatch

        updates: List[LiveOddsUpdate] = []
        now = datetime.now(timezone.utc)

        try:
            import httpx
            client = httpx.Client(timeout=10.0)

            # 获取竞彩在售期次
            url = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
            params = {"sellStatus": "1", "pageNo": "1", "pageSize": "50"}

            resp = client.get(url, params=params)
            if resp.status_code != 200:
                client.close()
                return []

            data = resp.json()
            client.close()

            # 解析赔率数据
            # (实际格式需根据竞彩API响应调整)
            matches_data = data.get("value", {}).get("matchResult", [])
            for md in matches_data:
                # 提取赔率 — 需要根据实际API响应结构解析
                pass

        except Exception as e:
            logger.debug(f"JingcaiLiveSource fetch error: {e}")

        return updates


# ─── 采集引擎 ───

class LiveOddsFeed:
    """
    滚球赔率采集引擎。

    支持多数据源，可配置采集间隔，采集结果:
    1. 存入 LiveOddsSnapshot 表
    2. 推送到 OddsBus
    3. 触发对冲检查

    用法:
    feed = LiveOddsFeed(db_session, bus=get_odds_bus())
    feed.start()  # 启动后台采集线程
    feed.stop()   # 停止
    """

    def __init__(
        self,
        session: Optional[Session] = None,
        bus: Optional[OddsBus] = None,
        poll_interval: int = 30,
        fast_interval: int = 10,
        pre_kickoff_minutes: int = 10,
        use_simulated: bool = True,
    ):
        self._bus = bus or get_odds_bus()
        self._poll_interval = poll_interval
        self._fast_interval = fast_interval
        self._pre_kickoff_minutes = pre_kickoff_minutes
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 初始化数据源
        self._sources: List[LiveOddsSource] = []
        if use_simulated:
            self._sources.append(SimulatedLiveSource())

        # 延迟初始化 API 源（需要 key）
        self._api_source: Optional[OddsApiLiveSource] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def _get_session() -> Session:
        """创建短期 DB session（线程安全，用完即关）。"""
        from models import SessionLocal
        return SessionLocal()

    def add_source(self, source: LiveOddsSource) -> None:
        """添加数据源。"""
        self._sources.append(source)

    def init_api_source(self, api_key: str) -> None:
        """初始化 Odds API 滚球源。"""
        if api_key:
            self._api_source = OddsApiLiveSource(api_key)
            self._sources.append(self._api_source)
            logger.info("OddsApiLiveSource initialized with API key")

    def start(self) -> None:
        """启动采集线程。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"LiveOddsFeed started (interval={self._poll_interval}s)")

    def stop(self) -> None:
        """停止采集线程。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("LiveOddsFeed stopped")

    def poll_once(self) -> int:
        """
        执行一次采集。返回更新的比赛数量。

        可被调度器调用，无需启动后台线程。
        """
        session = self._get_session()
        try:
            match_ids = self._get_live_match_ids(session)
            if not match_ids:
                return 0

            all_updates: List[LiveOddsUpdate] = []
            for source in self._sources:
                try:
                    updates = source.fetch_live_odds(match_ids, session)
                    all_updates.extend(updates)
                except Exception as e:
                    logger.error(f"Source {source.name} error: {e}")

            # 存入 DB + 推送到总线
            for update in all_updates:
                self._store_snapshot(update, session)
                self._bus.publish(update)

            return len(all_updates)
        finally:
            session.close()

    # ─── 内部方法 ───

    def _poll_loop(self) -> None:
        """后台采集循环。"""
        while self._running:
            try:
                n = self.poll_once()
                if n > 0:
                    logger.debug(f"LiveOddsFeed: {n} updates")
            except Exception as e:
                logger.error(f"LiveOddsFeed poll error: {e}")

            # 动态间隔: 临近开球用快速间隔
            interval = self._get_current_interval()
            time.sleep(interval)

    def _get_current_interval(self) -> float:
        """根据比赛状态决定采集间隔。"""
        now = datetime.now(timezone.utc)
        from models import Match

        session = self._get_session()
        try:
            matches = session.query(Match).filter(
                Match.status.in_(["scheduled", "upcoming", "live"])
            ).all()
        finally:
            session.close()

        for m in matches:
            if m.kickoff_at:
                kickoff = m.kickoff_at
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                delta = (kickoff - now).total_seconds() / 60
                if abs(delta) <= self._pre_kickoff_minutes:
                    return self._fast_interval

        return self._poll_interval

    def _get_live_match_ids(self, session: Session) -> List[int]:
        """获取需要采集的比赛ID。"""
        from models import Match

        matches = session.query(Match).filter(
            Match.status.in_(["scheduled", "upcoming", "live"])
        ).all()

        return [m.id for m in matches]

    def _store_snapshot(self, update: LiveOddsUpdate, session: Session) -> None:
        """将赔率快照存入 DB。"""
        from models import LiveOddsSnapshot

        snapshot = LiveOddsSnapshot(
            match_id=update.match_id,
            source=update.source,
            odds_home=update.odds_home,
            odds_draw=update.odds_draw,
            odds_away=update.odds_away,
            match_minute=update.match_minute,
            live_score_home=update.live_score_home,
            live_score_away=update.live_score_away,
            recorded_at=update.timestamp,
        )
        try:
            session.add(snapshot)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to store live odds snapshot: {e}")


# ─── 辅助函数 ───

def _change_pct(old: float, new: float) -> float:
    """计算赔率变动百分比。"""
    if old <= 0:
        return 0.0
    return (new - old) / old


def live_odds_update_to_dict(update: LiveOddsUpdate) -> Dict[str, Any]:
    """将 LiveOddsUpdate 转换为 API 响应 dict。"""
    return {
        "match_id": update.match_id,
        "source": update.source,
        "odds": {
            "home": update.odds_home,
            "draw": update.odds_draw,
            "away": update.odds_away,
        },
        "match_minute": update.match_minute,
        "score": {
            "home": update.live_score_home,
            "away": update.live_score_away,
        } if update.live_score_home is not None else None,
        "changes": {
            "home_pct": round(update.home_change_pct, 4),
            "draw_pct": round(update.draw_change_pct, 4),
            "away_pct": round(update.away_change_pct, 4),
        },
        "timestamp": update.timestamp.isoformat(),
    }
