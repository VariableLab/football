"""
BetExplorer 网页爬虫数据源
免费实时赔率数据源，无需 API key
"""
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from database.models import Match, OddsHistory
from utils.logger import get_logger
from .base import OddsSnapshot, OddsSource

logger = get_logger("odds.betexplorer")

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


class BetExplorerSource(OddsSource):
    """BetExplorer (betexplorer.com) 网页爬虫"""
    name = "betexplorer"
    BASE_URL = "https://www.betexplorer.com"

    def __init__(self):
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        self._cloak: Optional[Any] = None

    def _get_cloak(self):
        if self._cloak is None:
            try:
                from integrations.cloakbrowser_bridge import get_bridge
                bridge = get_bridge()
                if bridge.is_available():
                    self._cloak = bridge
                    logger.info("[betexplorer] cloakbrowser available")
            except Exception as e:
                logger.debug(f"[betexplorer] cloakbrowser not available: {e}")
        return self._cloak

    def _search_match(self, home_team: str, away_team: str) -> Optional[str]:
        """搜索比赛，返回比赛页面 URL"""
        try:
            resp = _fetch_with_retry(self.client, f"{self.BASE_URL}/gres/ajax-search.php",
                                     params={"q": home_team[:15]})
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
            json_patterns = [
                r'"odds":\s*\{[^}]*"1":\s*"([0-9.]+)"[^}]*"X":\s*"([0-9.]+)"[^}]*"2":\s*"([0-9.]+)"',
                r'"1":\s*"([0-9.]+)"[^}]*"X":\s*"([0-9.]+)"[^}]*"2":\s*"([0-9.]+)"',
            ]
            for pat in json_patterns:
                m = re.search(pat, html)
                if m:
                    return {"home": float(m.group(1)), "draw": float(m.group(2)), "away": float(m.group(3))}

            all_numbers = re.findall(r'>(\d+\.\d+)<', html)
            valid = [float(x) for x in all_numbers if 1.01 <= float(x) <= 50.0]
            if len(valid) >= 3:
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
        """获取单场比赛赔率"""
        home = match.home_team.name if match.home_team else ""
        away = match.away_team.name if match.away_team else ""
        if not home or not away:
            return None

        match_url = self._search_match(home, away)
        if not match_url:
            logger.debug(f"[betexplorer] {match.match_code}: search returned no results")
            return None

        cloak = self._get_cloak()
        if cloak:
            try:
                page = cloak.render_single(match_url, wait_selector=".table-main", wait_ms=2000)
                if page and page.html:
                    odds = self._parse_odds(page.html)
                    if odds:
                        logger.info(f"[betexplorer] {match.match_code}: cloakbrowser parsed odds")
                        return OddsSnapshot(
                            match_id=match.id, source=self.name,
                            odds_home=odds.get("home"), odds_draw=odds.get("draw"),
                            odds_away=odds.get("away"), recorded_at=datetime.now(timezone.utc),
                        )
            except Exception as e:
                logger.debug(f"[betexplorer] {match.match_code}: cloakbrowser failed, falling back: {e}")

        try:
            resp = _fetch_with_retry(self.client, match_url)
            resp.raise_for_status()
            odds = self._parse_odds(resp.text)
            if not odds:
                logger.debug(f"[betexplorer] {match.match_code}: could not parse odds from page")
                return None
            return OddsSnapshot(
                match_id=match.id, source=self.name,
                odds_home=odds.get("home"), odds_draw=odds.get("draw"),
                odds_away=odds.get("away"), recorded_at=datetime.now(timezone.utc),
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
