"""
赔率采集中心

支持数据源：
  1. football-data.co.uk — 免费历史CSV（国际比赛含赔率）
  2. Odds API — 付费实时赔率
  3. 竞彩官网 — 爬虫（基础框架）
  4. 澳门彩票 / 香港马会 — 爬虫（基础框架）

所有采集结果统一存入 Match.odds_* 字段和独立的 odds_history 表。
"""

import csv
import io
import json
import logging
import math
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from config import get_settings
from models import Match, AuditLog, OddsHistory

from logger import get_logger

logger = get_logger("odds")
settings = get_settings()

# ────────────────────────────
# HTTP 重试工具
# ────────────────────────────
_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)


def _fetch_with_retry(client: httpx.Client, url: str, *, params: dict = None,
                      max_retries: int = 3, base_delay: float = 1.0) -> httpx.Response:
    """带指数退避的 HTTP GET 重试"""
    for attempt in range(max_retries):
        try:
            return client.get(url, params=params)
        except _RETRYABLE_ERRORS as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(f"[retry] {url} attempt {attempt+1}/{max_retries} failed: {e}, retrying in {delay:.1f}s")
            time.sleep(delay)


# ────────────────────────────
# 数据结构
# ────────────────────────────
@dataclass
class OddsSnapshot:
    """某一时刻的赔率快照"""
    match_id: int
    source: str                    # bet365 / macau / hkjc / jc / oddsapi
    odds_home: float
    odds_draw: float
    odds_away: float
    recorded_at: datetime
    
    # 让球（如有）
    handicap: Optional[float] = None
    odds_home_hcp: Optional[float] = None
    odds_away_hcp: Optional[float] = None

    # 多玩法赔率（竞彩专用，JSON 格式）
    multi_pool_odds: Optional[Dict[str, Any]] = None


@dataclass
class OddsAnomaly:
    """赔率异动告警"""
    match_id: int
    source: str
    direction: str                 # home / draw / away
    old_odds: float
    new_odds: float
    change_pct: float
    severity: str                  # info / warning / critical


# ────────────────────────────
# Odds API 预算管理器
# ────────────────────────────
class OddsApiBudget:
    """
    管理 Odds API 免费套餐的 500 credits/月预算。
    数据持久化到 JSON 文件，按月自动重置。线程安全。
    """
    FREE_MONTHLY_CREDITS = 500
    BUDGET_FILE = Path(__file__).parent / ".odds_api_budget.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, int | str] = {"year_month": "", "used": 0}
        self._load()

    def _load(self):
        if self.BUDGET_FILE.exists():
            try:
                self._data = json.loads(self.BUDGET_FILE.read_text())
            except Exception as e:
                logger.warning(f"[odds-budget] Corrupt budget file, resetting: {e}")
                self._data = {"year_month": "", "used": 0}
        self._reset_if_new_month()

    def _reset_if_new_month(self):
        current_ym = datetime.now(timezone.utc).strftime("%Y-%m")
        if self._data.get("year_month") != current_ym:
            self._data = {"year_month": current_ym, "used": 0}
            self._save()
            logger.info(f"[odds-budget] New month reset: {current_ym}, credits reset to 0")

    def _save(self):
        try:
            self.BUDGET_FILE.write_text(json.dumps(self._data))
        except Exception as e:
            logger.error(f"[odds-budget] Failed to save budget: {e}")

    def can_spend(self, amount: int = 1) -> bool:
        with self._lock:
            self._reset_if_new_month()
            return self._data["used"] + amount <= self.FREE_MONTHLY_CREDITS

    def spend(self, amount: int = 1) -> bool:
        with self._lock:
            self._reset_if_new_month()
            if self._data["used"] + amount > self.FREE_MONTHLY_CREDITS:
                logger.warning(
                    f"[odds-budget] Budget exhausted: {self._data['used']}/{self.FREE_MONTHLY_CREDITS}"
                )
                return False
            self._data["used"] += amount
            self._save()
            logger.info(f"[odds-budget] Spent {amount} credit(s), remaining: {self.remaining()}")
            return True

    def remaining(self) -> int:
        with self._lock:
            self._reset_if_new_month()
            return self.FREE_MONTHLY_CREDITS - self._data["used"]

    def status(self) -> Dict:
        self._reset_if_new_month()
        return {
            "month": self._data["year_month"],
            "used": self._data["used"],
            "remaining": self.remaining(),
            "total": self.FREE_MONTHLY_CREDITS,
        }


# ────────────────────────────
# 抽象基类
# ────────────────────────────
class OddsSource(ABC):
    """赔率数据源抽象接口"""

    name: str = ""

    @abstractmethod
    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """获取单场比赛的最新赔率"""
        pass

    @abstractmethod
    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """批量获取（效率更高）"""
        pass

    def download_all(self, use_cache: bool = True) -> List[Dict]:
        """下载全部数据（仅部分源支持，如 football-data）"""
        return []


# ────────────────────────────
# Source 1: football-data.co.uk
# ────────────────────────────
class FootballDataSource(OddsSource):
    """
    football-data.co.uk 免费历史数据下载器。
    适合获取历史比赛的最终赔率，用于回测模型。
    
    数据格式：CSV，包含多赛季多联赛
    国际比赛：https://www.football-data.co.uk/new/newinternational.csv
    """
    name = "football-data"
    # football-data.co.uk 联赛代码映射（mmz4281/赛季/代码.csv）
    LEAGUE_URLS = {
        "epl_2324": "https://www.football-data.co.uk/mmz4281/2324/E0.csv",
        "epl_2223": "https://www.football-data.co.uk/mmz4281/2223/E0.csv",
        "epl_2122": "https://www.football-data.co.uk/mmz4281/2122/E0.csv",
    }
    BASE_URL = LEAGUE_URLS["epl_2324"]

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self._cache: Dict[str, Dict] = {}
        self._last_download: Optional[datetime] = None
        self._cache_ttl = 3600  # 1小时缓存

    def _parse_csv(self, content: str) -> List[Dict]:
        """解析 CSV 内容"""
        reader = csv.DictReader(io.StringIO(content))
        return list(reader)

    def download_all(self, use_cache: bool = True) -> List[Dict]:
        """下载全部国际比赛数据（含历史赔率）"""
        if use_cache and self._last_download:
            elapsed = (datetime.now(timezone.utc) - self._last_download).total_seconds()
            if elapsed < self._cache_ttl and self._cache:
                logger.info(f"[football-data] Using cache ({len(self._cache)} rows, {int(elapsed)}s old)")
                return list(self._cache.values())

        try:
            resp = _fetch_with_retry(self.client, self.BASE_URL)
            resp.raise_for_status()
            data = self._parse_csv(resp.text)
            # 更新缓存
            self._cache.clear()
            for row in data:
                key = f"{row.get('HomeTeam', '')}-{row.get('AwayTeam', '')}-{row.get('Date', '')}"
                self._cache[key] = row
            self._last_download = datetime.now(timezone.utc)
            logger.info(f"[football-data] Downloaded {len(data)} rows")
            return data
        except Exception as e:
            logger.error(f"[football-data] Download failed: {e}")
            # 失败时如果有缓存则返回缓存
            if self._cache:
                logger.info(f"[football-data] Fallback to cache ({len(self._cache)} rows)")
                return list(self._cache.values())
            return []

    def find_match_odds(
        self,
        data: List[Dict],
        home_team: str,
        away_team: str,
        date_str: str,          # YYYY-MM-DD
    ) -> Optional[Dict]:
        """
        在CSV数据中查找特定比赛的赔率。
        字段说明：
          B365H/B365D/B365A = Bet365 主/平/客
          BWH/BWD/BWA = Betway
          IWH/IWD/IWA = Interwetten
          PSH/PSD/PSA = Pinnacle
        """
        for row in data:
            # football-data 使用英文队名缩写，这里做模糊匹配
            row_home = row.get("HomeTeam", "").lower()
            row_away = row.get("AwayTeam", "").lower()
            row_date = row.get("Date", "")

            # 简单匹配（实际项目中应维护队名映射表）
            if home_team.lower() in row_home and away_team.lower() in row_away:
                # 日期格式可能是 DD/MM/YY
                if date_str.replace("-", "/") in row_date or date_str in row_date:
                    return {
                        "bet365_home": self._parse_float(row.get("B365H")),
                        "bet365_draw": self._parse_float(row.get("B365D")),
                        "bet365_away": self._parse_float(row.get("B365A")),
                        "pinnacle_home": self._parse_float(row.get("PSH")),
                        "pinnacle_draw": self._parse_float(row.get("PSD")),
                        "pinnacle_away": self._parse_float(row.get("PSA")),
                        "date": row_date,
                    }
        return None

    @staticmethod
    def _parse_float(val) -> Optional[float]:
        try:
            return float(val) if val else None
        except (ValueError, TypeError):
            return None

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """从缓存或下载的数据中查找"""
        if not self._cache:
            data = self.download_all()
            for row in data:
                key = f"{row.get('HomeTeam', '')}-{row.get('AwayTeam', '')}-{row.get('Date', '')}"
                self._cache[key] = row

        # 构建查找key
        key = f"{match.home_team.name}-{match.away_team.name}-{match.kickoff_at.strftime('%Y-%m-%d') if match.kickoff_at else ''}"
        row = self._cache.get(key)
        if not row:
            return None

        return OddsSnapshot(
            match_id=match.id,
            source=self.name,
            odds_home=self._parse_float(row.get("B365H")),
            odds_draw=self._parse_float(row.get("B365D")),
            odds_away=self._parse_float(row.get("B365A")),
            recorded_at=datetime.now(timezone.utc),
        )

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """批量获取历史数据"""
        results = []
        data = self.download_all()
        for match in matches:
            found = self.find_match_odds(
                data,
                match.home_team.name,
                match.away_team.name,
                match.kickoff_at.strftime("%Y-%m-%d") if match.kickoff_at else ""
            )
            if found:
                results.append(OddsSnapshot(
                    match_id=match.id,
                    source=self.name,
                    odds_home=found.get("bet365_home"),
                    odds_draw=found.get("bet365_draw"),
                    odds_away=found.get("bet365_away"),
                    recorded_at=datetime.now(timezone.utc),
                ))
        return results


# ────────────────────────────
# Source 2: Odds API
# ────────────────────────────
class OddsApiSource(OddsSource):
    """
    Odds API (the-odds-api.com) — 实时赔率API
    免费套餐：500 requests/month
    付费套餐：$29/month for 10K requests
    
    文档：https://the-odds-api.com/
    """
    name = "oddsapi"
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ODDS_API_KEY or ""
        self.client = httpx.Client(timeout=15.0, headers={"Accept": "application/json"})

    def _request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        if not self.api_key:
            logger.warning("[oddsapi] No API key configured")
            return None
        params = params or {}
        params["apiKey"] = self.api_key
        try:
            resp = _fetch_with_retry(self.client, f"{self.BASE_URL}/{endpoint}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("[oddsapi] Rate limited")
            else:
                logger.error(f"[oddsapi] HTTP error: {e}")
            return None
        except _RETRYABLE_ERRORS as e:
            logger.error(f"[oddsapi] Network error after retries: {e}")
            return None
        except Exception as e:
            logger.error(f"[oddsapi] Request failed: {e}")
            return None

    def get_sports(self) -> List[Dict]:
        """获取可用的运动项目列表"""
        data = self._request("sports")
        return data or []

    def get_odds(
        self,
        sport: str = "soccer_fifa_world_cup",
        regions: str = "eu",           # eu / us / uk / au
        markets: str = "h2h",          # h2h = 胜平负
    ) -> List[Dict]:
        """
        获取指定赛事的实时赔率。
        返回多场比赛 × 多博彩公司的数据结构。
        """
        return self._request(
            f"sports/{sport}/odds",
            params={
                "regions": regions,
                "markets": markets,
                "oddsFormat": "decimal",
            }
        ) or []

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """查找单场比赛的赔率"""
        odds_data = self.get_odds()
        for event in odds_data:
            # 匹配队名（需维护映射表）
            home_name = event.get("home_team", "").lower()
            away_name = event.get("away_team", "").lower()
            if match.home_team.name.lower() in home_name and match.away_team.name.lower() in away_name:
                # 取第一家博彩公司的赔率（通常是平均）
                bookmakers = event.get("bookmakers", [])
                if bookmakers:
                    first = bookmakers[0]
                    h2h = first.get("markets", [{}])[0].get("outcomes", [])
                    prices = {o["name"].lower(): o["price"] for o in h2h}
                    return OddsSnapshot(
                        match_id=match.id,
                        source=self.name,
                        odds_home=prices.get(match.home_team.name.lower()),
                        odds_draw=prices.get("draw"),
                        odds_away=prices.get(match.away_team.name.lower()),
                        recorded_at=datetime.now(timezone.utc),
                    )
        return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """批量获取：一次API调用获取全部比赛，再本地匹配"""
        odds_data = self.get_odds()
        if not odds_data:
            return []

        results = []
        match_map = {
            f"{m.home_team.name.lower()}-{m.away_team.name.lower()}": m
            for m in matches
        }

        for event in odds_data:
            key = f"{event.get('home_team', '').lower()}-{event.get('away_team', '').lower()}"
            match = match_map.get(key)
            if not match:
                continue

            bookmakers = event.get("bookmakers", [])
            if not bookmakers:
                continue

            # 取所有bookmaker的平均赔率
            home_prices, draw_prices, away_prices = [], [], []
            for bm in bookmakers:
                outcomes = bm.get("markets", [{}])[0].get("outcomes", [])
                for o in outcomes:
                    name = o["name"].lower()
                    price = o["price"]
                    if name == match.home_team.name.lower():
                        home_prices.append(price)
                    elif name == "draw":
                        draw_prices.append(price)
                    elif name == match.away_team.name.lower():
                        away_prices.append(price)

            if home_prices and draw_prices and away_prices:
                results.append(OddsSnapshot(
                    match_id=match.id,
                    source=self.name,
                    odds_home=sum(home_prices) / len(home_prices),
                    odds_draw=sum(draw_prices) / len(draw_prices),
                    odds_away=sum(away_prices) / len(away_prices),
                    recorded_at=datetime.now(timezone.utc),
                ))

        logger.info(f"[oddsapi] Fetched odds for {len(results)}/{len(matches)} matches")
        return results


# ────────────────────────────
# Source 3: 竞彩官网爬虫（基础框架）
# ────────────────────────────
class JingcaiSource(OddsSource):
    """
    中国体育彩票竞彩足球官网赔率（通过 webapi.sporttery.cn API）。

    API 端点: https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry
    参数: matchInfo=1, poolCode=had|hhad|crs|ttg|hafu, beginDate, endDate
    返回: JSON 格式，包含所有在售比赛的赔率数据

    支持5种玩法:
    - had:  胜平负 (h=主胜, d=平, a=客胜)
    - hhad: 让球胜平负 (同上 + goalLine=让球数)
    - crs:  比分 (s{i}s{j}=比分i:j赔率)
    - ttg:  总进球 (s0-s7=0-7+球赔率)
    - hafu: 半全场 (hh=主主, hd=主平, ha=主客, dh=平主...)
    """
    name = "jingcai"
    API_BASE = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"

    def __init__(self):
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.sporttery.cn/cn/football/match_list.html",
            },
        )
        self._cache: Dict[str, Any] = {}
        self._cache_date: Optional[str] = None

    def _fetch_pool(self, pool_code: str, begin_date: str, end_date: str) -> List[Dict]:
        """获取指定玩法的赔率数据"""
        params = {
            "matchInfo": "1",
            "poolCode": pool_code,
            "beginDate": begin_date,
            "endDate": end_date,
        }
        try:
            resp = self.client.get(self.API_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") and data.get("value", {}).get("matchInfoList"):
                return data["value"]["matchInfoList"]
        except Exception as e:
            logger.warning(f"[jingcai] Failed to fetch {pool_code}: {e}")
        return []

    def _fetch_all_pools(self, begin_date: str, end_date: str) -> Dict[int, Dict]:
        """
        获取所有5种玩法赔率，按 matchId 聚合。
        返回: {matchId: {had: {...}, hhad: {...}, crs: {...}, ttg: {...}, hafu: {...}, ...}}
        """
        matches: Dict[int, Dict] = {}

        for pool_code in ("had", "hhad", "crs", "ttg", "hafu"):
            date_groups = self._fetch_pool(pool_code, begin_date, end_date)
            for group in date_groups:
                for m in group.get("subMatchList", []):
                    mid = m.get("matchId")
                    if mid is None:
                        continue
                    if mid not in matches:
                        matches[mid] = {
                            "matchId": mid,
                            "matchNumStr": m.get("matchNumStr", ""),
                            "matchNum": m.get("matchNum", 0),
                            "matchDate": m.get("matchDate", ""),
                            "matchTime": m.get("matchTime", ""),
                            "matchStatus": m.get("matchStatus", ""),
                            "leagueAbbName": m.get("leagueAbbName", ""),
                            "leagueAllName": m.get("leagueAllName", ""),
                            "homeTeamAbbName": m.get("homeTeamAbbName", ""),
                            "homeTeamAllName": m.get("homeTeamAllName", ""),
                            "homeTeamCode": m.get("homeTeamCode", ""),
                            "awayTeamAbbName": m.get("awayTeamAbbName", ""),
                            "awayTeamAllName": m.get("awayTeamAllName", ""),
                            "awayTeamCode": m.get("awayTeamCode", ""),
                        }
                    matches[mid][pool_code] = m.get(pool_code, {})

        return matches

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """单场比赛查询 — 通过批量获取后按队名匹配"""
        snapshots = self.fetch_batch([match])
        return snapshots[0] if snapshots else None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """
        批量获取竞彩赔率。
        策略：按比赛日期范围一次拉取所有在售比赛，然后按队名匹配。
        """
        from datetime import datetime, timedelta

        if not matches:
            return []

        # 确定日期范围（从今天到最远比赛日期+1天）
        today = datetime.now().strftime("%Y-%m-%d")
        max_date = today
        for m in matches:
            if m.kickoff_at:
                d = m.kickoff_at.strftime("%Y-%m-%d")
                if d > max_date:
                    max_date = d
        end_date = (datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        # 检查缓存
        cache_key = f"{today}_{end_date}"
        if self._cache_date != cache_key or not self._cache:
            self._cache = self._fetch_all_pools(today, end_date)
            self._cache_date = cache_key

        results: List[OddsSnapshot] = []
        for match in matches:
            snap = self._match_to_snapshot(match, self._cache)
            if snap:
                results.append(snap)

        return results

    def _match_to_snapshot(self, match: Match, api_data: Dict[int, Dict]) -> Optional[OddsSnapshot]:
        """将 API 数据按队名匹配到 Match 对象"""
        home_name = match.home_team.name if match.home_team else ""
        away_name = match.away_team.name if match.away_team else ""

        best_match = None
        best_score = 0

        for mid, mdata in api_data.items():
            score = 0
            api_home = mdata.get("homeTeamAbbName", "") or mdata.get("homeTeamAllName", "")
            api_away = mdata.get("awayTeamAbbName", "") or mdata.get("awayTeamAllName", "")

            # 中文精确匹配
            if home_name == api_home:
                score += 2
            elif home_name in api_home or api_home in home_name:
                score += 1
            if away_name == api_away:
                score += 2
            elif away_name in api_away or api_away in away_name:
                score += 1

            if score > best_score:
                best_score = score
                best_match = mdata

        if not best_match or best_score < 2:
            return None

        had = best_match.get("had", {})
        hhad = best_match.get("hhad", {})

        odds_home = self._safe_float(had.get("h"))
        odds_draw = self._safe_float(had.get("d"))
        odds_away = self._safe_float(had.get("a"))

        if not (odds_home and odds_draw and odds_away):
            return None

        # 解析让球数
        handicap = 0
        goal_line = hhad.get("goalLine", "")
        if goal_line:
            try:
                handicap = int(float(goal_line))
            except (ValueError, TypeError):
                pass

        # 构建多玩法赔率 JSON
        multi_pool_odds = {
            "had": {
                "h": had.get("h"), "d": had.get("d"), "a": had.get("a"),
                "updateDate": had.get("updateDate", ""), "updateTime": had.get("updateTime", ""),
            },
            "hhad": {
                "h": hhad.get("h"), "d": hhad.get("d"), "a": hhad.get("a"),
                "goalLine": hhad.get("goalLine", ""),
                "updateDate": hhad.get("updateDate", ""), "updateTime": hhad.get("updateTime", ""),
            },
            "crs": best_match.get("crs", {}),
            "ttg": best_match.get("ttg", {}),
            "hafu": best_match.get("hafu", {}),
        }

        return OddsSnapshot(
            match_id=match.id,
            source="jingcai",
            odds_home=odds_home,
            odds_draw=odds_draw,
            odds_away=odds_away,
            recorded_at=datetime.now(timezone.utc),
            handicap=float(handicap),
            multi_pool_odds=multi_pool_odds,
        )

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    def close(self):
        self.client.close()

class MacauSource(OddsSource):
    """
    澳门彩票盘口数据爬虫。
    使用 cloakbrowser 渲染澳门盘口页面（JS 渲染 + 反爬绕过）。
    澳门盘口通常提供：让球盘 + 大小球。
    数据来源：澳门彩票官方网站 + 500.com 盘口页面
    """
    name = "macau"

    def __init__(self):
        self._cloak = None

    def _get_cloak(self):
        if self._cloak is None:
            try:
                from integrations.cloakbrowser_bridge import get_bridge
                bridge = get_bridge()
                if bridge.is_available():
                    self._cloak = bridge
            except Exception:
                pass
        return self._cloak

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        cloak = self._get_cloak()
        if not cloak:
            return None
        # 澳门盘口需 cloakbrowser JS 渲染，待确认网站结构后完善
        return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return []


# ────────────────────────────
# Source 5: 香港马会爬虫（基础框架）
# ────────────────────────────
class HKJCSource(OddsSource):
    """
    香港赛马会足球赔率爬虫。
    使用 cloakbrowser 渲染 HKJC 页面（JS 渲染 + 反爬绕过）。
    """
    name = "hkjc"

    def __init__(self):
        self._cloak = None

    def _get_cloak(self):
        if self._cloak is None:
            try:
                from integrations.cloakbrowser_bridge import get_bridge
                bridge = get_bridge()
                if bridge.is_available():
                    self._cloak = bridge
            except Exception:
                pass
        return self._cloak

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        cloak = self._get_cloak()
        if not cloak:
            return None
        # HKJC 需 cloakbrowser JS 渲染，待确认网站结构后完善
        return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return []


# ────────────────────────────
# Source 5b: 占位数据源（依赖未安装时的安全降级）
# ────────────────────────────
class _DummySource(OddsSource):
    """返回空结果的安全占位数据源，防止采集链路因单个源失败而崩溃。"""

    def __init__(self, name: str):
        self.name = name

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return []


# ────────────────────────────
# Source 6: 合成赔率（Elo 模型兜底）
# ────────────────────────────
class SyntheticOddsSource(OddsSource):
    """
    基于 Elo 等级分差生成合成赔率。
    当没有真实赔率数据源可用时，作为兜底方案。
    数学模型基于 Elo 期望胜率公式 + 市场 overround。
    """
    name = "synthetic"

    # 市场 overround（博彩公司利润率）
    DEFAULT_OVERROUND = 1.08  # 8%
    # 主场优势（Elo 分）
    HOME_ADVANTAGE_ELO = 65

    def __init__(self, overround: float = None):
        self.overround = overround or self.DEFAULT_OVERROUND

    def _calc_probs(self, elo_home: float, elo_away: float) -> Tuple[float, float, float]:
        """
        基于 Elo 分差计算胜/平/负概率。
        返回 (p_home, p_draw, p_away)，和为 1。
        """
        diff = elo_home - elo_away + self.HOME_ADVANTAGE_ELO

        # 主胜概率（标准 Elo 公式）
        p_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))

        # 平局概率：分差越小，平局概率越高
        # diff=0  -> draw ≈ 28%
        # diff=200 -> draw ≈ 20%
        # diff=400 -> draw ≈ 12%
        p_draw = 0.28 * math.exp(-abs(diff) / 280.0)

        # 归一化确保和为 1
        total = p_home + p_draw
        if total >= 1.0:
            p_home = p_home / total * 0.95
            p_draw = 0.05
        p_away = max(0.0, 1.0 - p_home - p_draw)

        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total

    def _probs_to_odds(self, p_home: float, p_draw: float, p_away: float) -> Tuple[float, float, float]:
        """概率转赔率，加入 overround"""
        return (
            round(1.0 / p_home * self.overround, 2) if p_home > 0 else 999.0,
            round(1.0 / p_draw * self.overround, 2) if p_draw > 0 else 999.0,
            round(1.0 / p_away * self.overround, 2) if p_away > 0 else 999.0,
        )

    def generate(self, match: Match) -> Optional[OddsSnapshot]:
        """为比赛生成合成赔率"""
        home_elo = match.home_team.elo if match.home_team else 1500
        away_elo = match.away_team.elo if match.away_team else 1500

        if not home_elo or not away_elo:
            return None

        p_h, p_d, p_a = self._calc_probs(home_elo, away_elo)
        o_h, o_d, o_a = self._probs_to_odds(p_h, p_d, p_a)

        return OddsSnapshot(
            match_id=match.id,
            source=self.name,
            odds_home=o_h,
            odds_draw=o_d,
            odds_away=o_a,
            recorded_at=datetime.now(timezone.utc),
        )

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        return self.generate(match)

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return [snap for m in matches if (snap := self.generate(m))]


# ────────────────────────────
# Source 7: BetExplorer 网页爬虫
# ────────────────────────────
class BetExplorerSource(OddsSource):
    """
    BetExplorer (betexplorer.com) 网页爬虫。
    免费实时赔率数据源，无需 API key。
    优先使用 cloakbrowser (JS 渲染) 获取页面，降级到 httpx 直连。
    """
    name = "betexplorer"
    BASE_URL = "https://www.betexplorer.com"

    def __init__(self):
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        self._cloak: Optional[Any] = None

    def _get_cloak(self):
        """懒加载 cloakbrowser 桥接"""
        if self._cloak is None:
            try:
                from integrations.cloakbrowser_bridge import get_bridge
                bridge = get_bridge()
                if bridge.is_available():
                    self._cloak = bridge
                    logger.info("[betexplorer] cloakbrowser available, will use for JS-rendered pages")
            except Exception as e:
                logger.debug(f"[betexplorer] cloakbrowser not available: {e}")
        return self._cloak

    def _search_match(self, home_team: str, away_team: str) -> Optional[str]:
        """搜索比赛，返回比赛页面 URL"""
        try:
            # BetExplorer 搜索接口返回 JSON 数组
            resp = _fetch_with_retry(
                self.client,
                f"{self.BASE_URL}/gres/ajax-search.php",
                params={"q": home_team[:15]}
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if not isinstance(data, list):
                return None

            home_l = home_team.lower()
            away_l = away_team.lower()
            for item in data:
                if isinstance(item, dict):
                    title = item.get("title", "").lower()
                    if home_l in title and away_l in title:
                        url = item.get("url", "")
                        if url and url.startswith("/"):
                            return f"{self.BASE_URL}{url}"
                        return url
            return None
        except Exception as e:
            logger.warning(f"[betexplorer] Search failed: {e}")
            return None

    def _parse_odds(self, html: str) -> Optional[Dict[str, float]]:
        """从比赛页面 HTML 解析 1x2 平均赔率"""
        try:
            import re

            # 策略 1: 找页面中 JSON 格式的赔率数据
            # BetExplorer 页面里有时会有类似 odds 的 JSON 块
            json_patterns = [
                r'"odds":\s*\{[^}]*"1":\s*"([0-9.]+)"[^}]*"X":\s*"([0-9.]+)"[^}]*"2":\s*"([0-9.]+)"',
                r'"1":\s*"([0-9.]+)"[^}]*"X":\s*"([0-9.]+)"[^}]*"2":\s*"([0-9.]+)"',
            ]
            for pat in json_patterns:
                m = re.search(pat, html)
                if m:
                    return {
                        "home": float(m.group(1)),
                        "draw": float(m.group(2)),
                        "away": float(m.group(3)),
                    }

            # 策略 2: 找 table 中的赔率数字
            # BetExplorer 的 1x2 赔率通常在 .table-main 表格里
            # 先找所有 1.01 - 50.0 范围内的数字
            all_numbers = re.findall(r'>(\d+\.\d+)<', html)
            valid = [float(x) for x in all_numbers if 1.01 <= float(x) <= 50.0]

            if len(valid) >= 3:
                # 通常页面上会按 主/平/客 顺序出现多次
                # 取前三个不同的值
                seen = []
                for v in valid:
                    if not any(abs(v - s) < 0.01 for s in seen):
                        seen.append(v)
                    if len(seen) >= 3:
                        break
                if len(seen) >= 3:
                    return {"home": seen[0], "draw": seen[1], "away": seen[2]}

            return None
        except Exception as e:
            logger.warning(f"[betexplorer] Parse failed: {e}")
            return None

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """获取单场比赛赔率 — 优先 cloakbrowser JS 渲染，降级 httpx"""
        home = match.home_team.name if match.home_team else ""
        away = match.away_team.name if match.away_team else ""
        if not home or not away:
            return None

        match_url = self._search_match(home, away)
        if not match_url:
            logger.debug(f"[betexplorer] {match.match_code}: search returned no results")
            return None

        # Try cloakbrowser first (JS rendering, bypasses anti-bot)
        cloak = self._get_cloak()
        if cloak:
            try:
                page = cloak.render_single(match_url, wait_selector=".table-main", wait_ms=2000)
                if page and page.html:
                    odds = self._parse_odds(page.html)
                    if odds:
                        logger.info(f"[betexplorer] {match.match_code}: cloakbrowser parsed odds")
                        return OddsSnapshot(
                            match_id=match.id,
                            source=self.name,
                            odds_home=odds.get("home"),
                            odds_draw=odds.get("draw"),
                            odds_away=odds.get("away"),
                            recorded_at=datetime.now(timezone.utc),
                        )
            except Exception as e:
                logger.debug(f"[betexplorer] {match.match_code}: cloakbrowser failed, falling back: {e}")

        # Fallback: httpx direct
        try:
            resp = _fetch_with_retry(self.client, match_url)
            resp.raise_for_status()

            odds = self._parse_odds(resp.text)
            if not odds:
                logger.debug(f"[betexplorer] {match.match_code}: could not parse odds from page")
                return None

            return OddsSnapshot(
                match_id=match.id,
                source=self.name,
                odds_home=odds.get("home"),
                odds_draw=odds.get("draw"),
                odds_away=odds.get("away"),
                recorded_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning(f"[betexplorer] Fetch failed for {match.match_code}: {e}")
            return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """批量获取（逐个获取，有礼貌延迟）"""
        results = []
        for match in matches:
            snap = self.fetch(match)
            if snap:
                results.append(snap)
            time.sleep(0.8)
        return results


# ────────────────────────────
# 采集调度器
# ────────────────────────────
class OddsCollector:
    """
    统一管理多个数据源的赔率采集。
    分级策略：
      Tier 1 (Primary):  football-data 缓存 + 数据新鲜度检查（每2小时）
      Tier 2 (Premium):  Odds API 全量采集（每天2次）
      Tier 3 (Focus):    Odds API 焦点战加采（每天1次 + 赛前4h自动）
    """

    def __init__(self, db: Session, budget: Optional[OddsApiBudget] = None):
        self.db = db
        self.sources: Dict[str, OddsSource] = {}
        self.budget = budget or OddsApiBudget()
        self._init_sources()

    def _init_sources(self):
        """初始化所有可用的数据源"""
        self.sources["oddsapi"] = OddsApiSource()
        self.sources["football-data"] = FootballDataSource()
        self.sources["jingcai"] = JingcaiSource()
        self.sources["macau"] = MacauSource()
        self.sources["hkjc"] = HKJCSource()
        self.sources["betexplorer"] = BetExplorerSource()
        self.sources["oddsharvester"] = self._init_oddsharvester()
        self.sources["synthetic"] = SyntheticOddsSource()
        self.sources["zgzcw"] = self._init_zgzcw()
        self.sources["500"] = self._init_wubaibai()

    def _init_zgzcw(self):
        """懒加载 ZgzcwOddsSource（zgzcw.com 百家欧赔）"""
        try:
            from zgzcw_source import ZgzcwOddsSource
            source = ZgzcwOddsSource()
            logger.info("[zgzcw] Source initialized")
            return source
        except Exception as e:
            logger.warning(f"[zgzcw] Failed to initialize: {e}")
            return _DummySource("zgzcw")

    def _init_wubaibai(self):
        """懒加载 WubaibaiOddsSource（500.com 百家欧赔）"""
        try:
            from wubaibai_source import WubaibaiOddsSource
            source = WubaibaiOddsSource()
            logger.info("[500] Source initialized")
            return source
        except Exception as e:
            logger.warning(f"[500] Failed to initialize: {e}")
            return _DummySource("500")

    def _init_oddsharvester(self):
        """懒加载 OddsHarvester + cloakbrowser（oddsportal.com 隐身渲染）"""
        try:
            from integrations.oddsharvester_bridge import OddsHarvesterSourceAdapter
            adapter = OddsHarvesterSourceAdapter()
            # 预加载常用联赛缓存（如果已抓取过）
            adapter._load_cache("soccer/world/world-cup", "2022")
            # 尝试 cloakbrowser 初始化
            cloak = adapter._get_cloak()
            if cloak:
                logger.info("[oddsharvester] Adapter initialized with cloakbrowser")
            else:
                logger.info("[oddsharvester] Adapter initialized (cloakbrowser not available, using CLI/cache)")
            return adapter
        except Exception as e:
            logger.warning(f"[oddsharvester] Failed to initialize: {e}")
            return _DummySource("oddsharvester")

    def collect_tier1_primary(self, matches: List[Match]) -> Dict:
        """
        Tier 1: 免费/基础层
        - football-data 缓存更新（内部1小时缓存，避免重复下载）
        - BetExplorer 爬虫批量采集
        - 未覆盖的比赛使用合成赔率兜底
        - 自动更新 Match 表主赔率字段
        """
        stale_matches = 0
        updated_count = 0

        # 1. football-data 缓存刷新（有内部1h缓存）
        fd_source = self.sources.get("football-data")
        if fd_source:
            try:
                fd_source.download_all(use_cache=True)
                logger.info("[collect-tier1] football-data cache refreshed")
            except Exception as e:
                logger.error(f"[collect-tier1] football-data failed: {e}")

        # 2. 批量采集（优先 BetExplorer + 合成兜底）
        batch_results = self.collect_batch(matches)

        for match in matches:
            sources = batch_results.get(match.id, {})
            if not sources:
                stale_matches += 1
                continue

            self.update_match_primary_odds(match, sources)
            updated_count += 1

        return {
            "stale_matches": stale_matches,
            "updated_count": updated_count,
            "total_matches": len(matches),
            "budget_remaining": self.budget.remaining(),
        }

    def collect_tier2_premium(self, matches: List[Match]) -> Dict:
        """
        Tier 2: Odds API 全量采集
        - 每天2次（08:00, 20:00），覆盖全部 upcoming 比赛
        - 1 request = 1 credit
        """
        if not self.budget.can_spend(1):
            logger.warning("[collect-tier2] Odds API budget exhausted, skipping premium collection")
            return {"skipped": True, "reason": "budget_exhausted", "credits_used": 0, "budget_remaining": 0}

        oddsapi = self.sources.get("oddsapi")
        if not oddsapi:
            return {"skipped": True, "reason": "oddsapi_not_configured", "credits_used": 0, "budget_remaining": 0}

        snapshots = oddsapi.fetch_batch(matches)
        if not self.budget.spend(1):
            return {"skipped": True, "reason": "budget_spend_failed", "credits_used": 0, "budget_remaining": 0}

        all_anomalies = []
        for snap in snapshots:
            match = next((m for m in matches if m.id == snap.match_id), None)
            if match:
                self.update_match_primary_odds(match, {"oddsapi": snap})
                anomalies = self.detect_anomalies(match, {"oddsapi": snap})
                all_anomalies.extend(anomalies)

        return {
            "matches_count": len(snapshots),
            "credits_used": 1,
            "budget_remaining": self.budget.remaining(),
            "anomalies": all_anomalies,
        }

    def collect_tier3_focus(self, matches: List[Match]) -> Dict:
        """
        Tier 3: 焦点战加采
        - 只采集赛前4小时内的比赛
        - 或高关注度比赛（淘汰赛等）
        """
        now = datetime.now(timezone.utc)
        focus_matches = [
            m for m in matches
            if m.kickoff_at and 0 < ((m.kickoff_at.replace(tzinfo=timezone.utc) if m.kickoff_at.tzinfo is None else m.kickoff_at) - now).total_seconds() <= 4 * 3600
        ]

        if not focus_matches:
            logger.info("[collect-tier3] No focus matches within 4h")
            return {"skipped": True, "reason": "no_focus_matches"}
        if not self.budget.can_spend(1):
            logger.warning("[collect-tier3] Odds API budget exhausted, skipping focus collection")
            return {"skipped": True, "reason": "budget_exhausted", "credits_used": 0, "budget_remaining": 0}

        oddsapi = self.sources.get("oddsapi")
        if not oddsapi:
            return {"skipped": True, "reason": "oddsapi_not_configured"}

        snapshots = oddsapi.fetch_batch(focus_matches)
        self.budget.spend(1)

        all_anomalies = []
        for snap in snapshots:
            match = next((m for m in focus_matches if m.id == snap.match_id), None)
            if match:
                self.update_match_primary_odds(match, {"oddsapi-focus": snap})
                anomalies = self.detect_anomalies(match, {"oddsapi-focus": snap})
                all_anomalies.extend(anomalies)

        return {
            "matches_count": len(snapshots),
            "credits_used": 1,
            "budget_remaining": self.budget.remaining(),
            "anomalies": all_anomalies,
        }

    def collect_for_match(self, match: Match) -> Dict[str, OddsSnapshot]:
        """
        为单场比赛采集全部可用数据源的赔率。
        优先使用真实数据源，全部失败时 fallback 到合成赔率。
        返回 {source_name: snapshot} 字典。
        """
        results = {}
        has_real_odds = False

        # 1. 先尝试真实数据源（按优先级排序）
        real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
        for name in real_sources:
            source = self.sources.get(name)
            if not source:
                continue
            try:
                snapshot = source.fetch(match)
                if snapshot and all(v is not None for v in [snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away]):
                    results[name] = snapshot
                    self._store_snapshot(snapshot, is_closing=False)
                    has_real_odds = True
                    logger.info(f"[collect] {match.match_code} | {name}: {snapshot.odds_home}/{snapshot.odds_draw}/{snapshot.odds_away}")
                else:
                    logger.debug(f"[collect] {match.match_code} | {name}: no data")
            except Exception as e:
                logger.warning(f"[collect] {match.match_code} | {name} failed: {e}")

        # 2. 如果没有真实赔率，使用合成赔率兜底
        if not has_real_odds:
            synth = self.sources.get("synthetic")
            if synth:
                try:
                    snapshot = synth.fetch(match)
                    if snapshot and all(v is not None for v in [snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away]):
                        results["synthetic"] = snapshot
                        self._store_snapshot(snapshot, is_closing=False)
                        logger.info(f"[collect] {match.match_code} | synthetic: {snapshot.odds_home}/{snapshot.odds_draw}/{snapshot.odds_away}")
                except Exception as e:
                    logger.warning(f"[collect] {match.match_code} | synthetic failed: {e}")

        return results

    def collect_batch(self, matches: List[Match]) -> Dict[int, Dict[str, OddsSnapshot]]:
        """
        批量采集（优先使用各数据源的批量接口）。
        真实源失败的比赛用合成赔率兜底。
        返回 {match_id: {source_name: snapshot}}。
        """
        results: Dict[int, Dict[str, OddsSnapshot]] = {m.id: {} for m in matches}
        matched_ids: set[int] = set()

        # 1. 先跑真实数据源
        real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
        for name in real_sources:
            source = self.sources.get(name)
            if not source:
                continue
            try:
                snapshots = source.fetch_batch(matches)
                for snap in snapshots:
                    if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                        results[snap.match_id][name] = snap
                        self._store_snapshot(snap, is_closing=False)
                        matched_ids.add(snap.match_id)
                logger.info(f"[collect-batch] {name}: {len(snapshots)} matches")
            except Exception as e:
                logger.warning(f"[collect-batch] {name} failed: {e}")

        # 2. 未匹配到的比赛用合成赔率兜底
        unmatched = [m for m in matches if m.id not in matched_ids]
        if unmatched:
            synth = self.sources.get("synthetic")
            if synth:
                try:
                    snapshots = synth.fetch_batch(unmatched)
                    for snap in snapshots:
                        if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                            results[snap.match_id]["synthetic"] = snap
                            self._store_snapshot(snap, is_closing=False)
                    logger.info(f"[collect-batch] synthetic: {len(snapshots)} matches (fallback)")
                except Exception as e:
                    logger.warning(f"[collect-batch] synthetic fallback failed: {e}")

        return results

    def update_match_primary_odds(self, match: Match, sources: Dict[str, OddsSnapshot], is_closing: bool = False):
        """
        将多源赔率汇总后，更新 Match 表的主赔率字段。
        策略：取有数据的源的平均值。
        同时记录 odds_source（优先级最高的真实源，或 synthetic）。

        当 is_closing=True 时，同时更新 closing_odds_*（真实收盘赔率），
        并排除 synthetic 源，确保收盘赔率只来自真实市场。
        """
        if not sources:
            return

        # ─── 普通赔率更新（含合成兜底） ───
        homes, draws, aways = [], [], []
        source_names = []
        for name, snap in sources.items():
            homes.append(snap.odds_home)
            draws.append(snap.odds_draw)
            aways.append(snap.odds_away)
            source_names.append(name)

        if homes and draws and aways:
            match.odds_home = round(sum(homes) / len(homes), 2)
            match.odds_draw = round(sum(draws) / len(draws), 2)
            match.odds_away = round(sum(aways) / len(aways), 2)
            # 记录来源：优先真实源，否则 synthetic
            real = [n for n in source_names if n != "synthetic"]
            match.odds_source = real[0] if real else "synthetic"

        # ─── 收盘赔率更新（仅真实源） ───
        if is_closing:
            real_sources = {
                name: snap for name, snap in sources.items()
                if name != "synthetic"
            }
            if real_sources:
                r_homes = [s.odds_home for s in real_sources.values()]
                r_draws = [s.odds_draw for s in real_sources.values()]
                r_aways = [s.odds_away for s in real_sources.values()]
                match.closing_odds_home = round(sum(r_homes) / len(r_homes), 2)
                match.closing_odds_draw = round(sum(r_draws) / len(r_draws), 2)
                match.closing_odds_away = round(sum(r_aways) / len(r_aways), 2)
                match.closing_odds_source = list(real_sources.keys())[0]
                match.odds_locked_at = datetime.now(timezone.utc)
                logger.info(
                    f"[odds-closing] {match.match_code}: "
                    f"{match.closing_odds_home}/{match.closing_odds_draw}/{match.closing_odds_away} "
                    f"(source: {match.closing_odds_source})"
                )

        self.db.commit()
        if not is_closing:
            logger.info(f"[odds-update] {match.match_code}: {match.odds_home}/{match.odds_draw}/{match.odds_away} (source: {match.odds_source})")
        
        # 赔率更新后触发预测重算（含防抖）
        try:
            from prediction_recalc import on_odds_updated
            on_odds_updated(self.db, match.id)
        except Exception as e:
            logger.warning(f"[odds-update] Prediction recalc trigger failed: {e}")

    def detect_anomalies(
        self,
        match: Match,
        new_sources: Dict[str, OddsSnapshot],
        threshold: float = 0.10,
    ) -> List[OddsAnomaly]:
        """
        检测赔率异动。
        对比上一次存储的赔率，变化超过 threshold（10%）则告警。
        """
        anomalies = []
        # 从数据库读取上一次赔率（简化：直接用 Match 表的当前值）
        prev = {
            "home": match.odds_home,
            "draw": match.odds_draw,
            "away": match.odds_away,
        }

        if not all(v is not None for v in prev.values()):
            return anomalies

        for source_name, snap in new_sources.items():
            for direction, new_val, old_val in [
                ("home", snap.odds_home, prev["home"]),
                ("draw", snap.odds_draw, prev["draw"]),
                ("away", snap.odds_away, prev["away"]),
            ]:
                if old_val is None or old_val == 0 or new_val is None:
                    continue
                change = abs(new_val - old_val) / old_val
                if change > threshold:
                    severity = "critical" if change > 0.20 else "warning" if change > 0.10 else "info"
                    anomalies.append(OddsAnomaly(
                        match_id=match.id,
                        source=source_name,
                        direction=direction,
                        old_odds=old_val,
                        new_odds=new_val,
                        change_pct=change,
                        severity=severity,
                    ))

        return anomalies

    def _store_snapshot(self, snapshot: OddsSnapshot, is_closing: bool = False):
        """存储赔率快照到 OddsHistory 表，自动去重 (5min窗口)。"""
        from data_cleaner import validate_source, validate_odds
        source = validate_source(snapshot.source)
        is_real = source != "synthetic"

        # 赔率校验
        h, d, a, valid = validate_odds(snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away)
        if not valid:
            return

        # Dedup: skip if already have a snapshot from same source within 5min
        cutoff = snapshot.recorded_at - timedelta(minutes=5)
        exists = self.db.query(OddsHistory).filter(
            OddsHistory.match_id == snapshot.match_id,
            OddsHistory.source == snapshot.source,
            OddsHistory.recorded_at >= cutoff,
        ).first()
        if exists:
            return

        history = OddsHistory(
            match_id=snapshot.match_id,
            source=source,
            odds_home=snapshot.odds_home,
            odds_draw=snapshot.odds_draw,
            odds_away=snapshot.odds_away,
            recorded_at=snapshot.recorded_at,
            is_closing=is_closing,
            is_real=is_real,
        )
        self.db.add(history)
        self.db.commit()

    def collect_closing_odds(self, matches: List[Match]) -> Dict:
        """
        采集收盘赔率 — 赛前最后一批真实赔率。
        应在赛前 15~60 分钟调用，标记 is_closing=True。
        只使用真实数据源（排除 synthetic）。
        """
        now = datetime.now(timezone.utc)
        # 筛选赛前 15~90 分钟的比赛
        closing_matches = [
            m for m in matches
            if m.kickoff_at and 0 < (m.kickoff_at - now).total_seconds() <= 90 * 60
        ]
        if not closing_matches:
            return {"skipped": True, "reason": "no_closing_window_matches"}

        results = []
        for match in closing_matches:
            # 只使用真实源采集收盘赔率
            real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
            best_snap = None
            best_source = None
            for name in real_sources:
                source = self.sources.get(name)
                if not source:
                    continue
                try:
                    snap = source.fetch(match)
                    if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                        best_snap = snap
                        best_source = name
                        break  # 按优先级取第一个成功源即可
                except Exception as e:
                    logger.debug(f"[closing-odds] {match.match_code} | {name} failed: {e}")

            if best_snap:
                self._store_snapshot(best_snap, is_closing=True)
                self.update_match_primary_odds(match, {best_source: best_snap}, is_closing=True)
                results.append(match.match_code)
            else:
                logger.warning(f"[closing-odds] {match.match_code}: no real odds available")

        return {
            "matches_processed": len(closing_matches),
            "matches_updated": len(results),
            "updated_codes": results,
        }


# ────────────────────────────
# 分级采集便捷函数（供 scheduler 调用）
# ────────────────────────────
def _get_upcoming_matches(db: Session, hours: int = 72) -> List[Match]:
    """获取指定时间窗口内的 upcoming 比赛"""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours)
    return db.query(Match).filter(
        Match.kickoff_at.between(now, window_end),
        Match.status.in_(["scheduled", "upcoming"])
    ).all()


def collect_odds_tier1_primary(db: Session) -> Dict:
    """
    Tier 1: 基础数据检查（每2小时）
    - football-data 缓存刷新
    - 检查本地数据新鲜度
    """
    matches = _get_upcoming_matches(db, hours=72)
    if not matches:
        logger.info("[odds-tier1] No upcoming matches")
        return {"stale_matches": 0, "budget_remaining": 0}

    collector = OddsCollector(db)
    return collector.collect_tier1_primary(matches)


def collect_odds_tier2_premium(db: Session) -> Dict:
    """
    Tier 2: Odds API 全量采集（每天2次）
    - 覆盖全部 upcoming 比赛
    - 消耗 1 credit
    """
    matches = _get_upcoming_matches(db, hours=72)
    if not matches:
        logger.info("[odds-tier2] No upcoming matches")
        return {"skipped": True, "reason": "no_matches"}

    collector = OddsCollector(db)
    return collector.collect_tier2_premium(matches)


def collect_odds_tier3_focus(db: Session) -> Dict:
    """
    Tier 3: 焦点战加采（每天1次 + 赛前4h自动）
    - 只采集赛前4小时内的比赛
    - 消耗 1 credit
    """
    matches = _get_upcoming_matches(db, hours=24)
    if not matches:
        logger.info("[odds-tier3] No upcoming matches")
        return {"skipped": True, "reason": "no_matches"}

    collector = OddsCollector(db)
    return collector.collect_tier3_focus(matches)


# 保留旧接口（向后兼容）
def collect_closing_odds_for_upcoming(db: Session, hours: int = 4) -> Dict:
    """
    便捷函数：采集即将开始比赛的收盘赔率。
    供 scheduler 直接调用。
    """
    matches = _get_upcoming_matches(db, hours=hours)
    if not matches:
        return {"skipped": True, "reason": "no_matches"}

    collector = OddsCollector(db)
    return collector.collect_closing_odds(matches)


def collect_odds_for_upcoming_matches(db: Session, hours: int = 72) -> List[OddsAnomaly]:
    """
    【兼容接口】统一采集（会消耗 credits，建议改用分级接口）
    """
    matches = _get_upcoming_matches(db, hours=hours)
    if not matches:
        return []

    collector = OddsCollector(db)
    batch_results = collector.collect_batch(matches)

    all_anomalies = []
    for match in matches:
        sources = batch_results.get(match.id, {})
        if not sources:
            sources = collector.collect_for_match(match)
        if sources:
            collector.update_match_primary_odds(match, sources)
            anomalies = collector.detect_anomalies(match, sources)
            all_anomalies.extend(anomalies)

    return all_anomalies


# ────────────────────────────
# CLI 测试
# ────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("赔率采集器 — 分级策略测试")
    print("=" * 60)

    # 1. 测试预算管理器
    print("\n1. Testing OddsApiBudget...")
    budget = OddsApiBudget()
    print(f"   Status: {budget.status()}")

    # 2. 测试 football-data.co.uk（带缓存）
    print("\n2. Testing football-data.co.uk...")
    fd = FootballDataSource()
    data = fd.download_all(use_cache=True)
    if data:
        print(f"   Downloaded {len(data)} rows")
        sample = fd.find_match_odds(data, "Argentina", "France", "2022-12-18")
        if sample:
            print(f"   Found ARG-FRA final: {sample}")
        else:
            print("   ARG-FRA not found (try different date format)")
    else:
        print("   Failed to download")

    # 3. 测试 Odds API（需要 key）
    print("\n3. Testing Odds API...")
    odds_api = OddsApiSource()
    if odds_api.api_key:
        sports = odds_api.get_sports()
        print(f"   Available sports: {len(sports)}")
        wc = [s for s in sports if "world_cup" in s.get("key", "")]
        if wc:
            print(f"   World Cup sport key: {wc[0]['key']}")
        else:
            print("   No world cup sport found (check sport key naming)")
    else:
        print("   No API key configured — skipping")

    print("\n✅ 测试完成")
    print("\n分级策略说明：")
    print("  Tier 1 (Primary):  每2小时 — 基础检查 + football-data 缓存")
    print("  Tier 2 (Premium):  每天08:00,20:00 — Odds API 全量采集 (1 credit/次)")
    print("  Tier 3 (Focus):    每天12:00 + 赛前4h — Odds API 焦点加采 (1 credit/次)")
    print("  免费额度 500 credits/月，当前策略约消耗 90 credits/月")
