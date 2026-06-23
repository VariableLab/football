"""
Odds API (the-odds-api.com) 数据源
付费实时赔率API，免费套餐 500 requests/month
"""
import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from database.models import Match, OddsHistory
from utils.logger import get_logger
from .base import OddsSnapshot, OddsSource

logger = get_logger("odds.oddsapi")

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


class OddsApiSource(OddsSource):
    """Odds API (the-odds-api.com) — 实时赔率API"""
    name = "oddsapi"
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ""
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

    def get_odds(self, sport: str = "soccer_fifa_world_cup",
                 regions: str = "eu", markets: str = "h2h") -> List[Dict]:
        """获取指定赛事的实时赔率"""
        return self._request(f"sports/{sport}/odds", params={
            "regions": regions, "markets": markets, "oddsFormat": "decimal",
        }) or []

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """查找单场比赛的赔率"""
        odds_data = self.get_odds()
        for event in odds_data:
            home_name = event.get("home_team", "").lower()
            away_name = event.get("away_team", "").lower()
            if match.home_team.name.lower() in home_name and match.away_team.name.lower() in away_name:
                bookmakers = event.get("bookmakers", [])
                if bookmakers:
                    first = bookmakers[0]
                    h2h = first.get("markets", [{}])[0].get("outcomes", [])
                    prices = {o["name"].lower(): o["price"] for o in h2h}
                    return OddsSnapshot(
                        match_id=match.id, source=self.name,
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
                    match_id=match.id, source=self.name,
                    odds_home=sum(home_prices) / len(home_prices),
                    odds_draw=sum(draw_prices) / len(draw_prices),
                    odds_away=sum(away_prices) / len(away_prices),
                    recorded_at=datetime.now(timezone.utc),
                ))

        logger.info(f"[oddsapi] Fetched odds for {len(results)}/{len(matches)} matches")
        return results
