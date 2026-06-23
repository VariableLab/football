"""
Football-Data.co.uk 数据源
免费历史CSV，适合获取历史比赛的最终赔率用于回测。
"""
import csv
import io
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from database.models import Match, OddsHistory
from utils.logger import get_logger
from .base import OddsSnapshot, OddsSource

logger = get_logger("odds.football-data")

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


class FootballDataSource(OddsSource):
    """
    football-data.co.uk 免费历史数据下载器。
    适合获取历史比赛的最终赔率，用于回测模型。
    """
    name = "football-data"
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
            self._cache.clear()
            for row in data:
                key = f"{row.get('HomeTeam', '')}-{row.get('AwayTeam', '')}-{row.get('Date', '')}"
                self._cache[key] = row
            self._last_download = datetime.now(timezone.utc)
            logger.info(f"[football-data] Downloaded {len(data)} rows")
            return data
        except Exception as e:
            logger.error(f"[football-data] Download failed: {e}")
            if self._cache:
                logger.info(f"[football-data] Fallback to cache ({len(self._cache)} rows)")
                return list(self._cache.values())
            return []

    def find_match_odds(self, data: List[Dict], home_team: str, away_team: str, date_str: str) -> Optional[Dict]:
        """在CSV数据中查找特定比赛的赔率"""
        for row in data:
            row_home = row.get("HomeTeam", "").lower()
            row_away = row.get("AwayTeam", "").lower()
            row_date = row.get("Date", "")
            if home_team.lower() in row_home and away_team.lower() in row_away:
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
            found = self.find_match_odds(data, match.home_team.name, match.away_team.name,
                                         match.kickoff_at.strftime("%Y-%m-%d") if match.kickoff_at else "")
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
