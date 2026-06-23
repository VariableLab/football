"""
竞彩官网 (sporttery.cn) 数据源
中国体育彩票竞彩足球官网赔率，免费、无 API key
"""
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from database.models import Match, OddsHistory
from utils.logger import get_logger
from .base import OddsSnapshot, OddsSource

logger = get_logger("odds.jingcai")


class JingcaiSource(OddsSource):
    """
    中国体育彩票竞彩足球官网赔率（通过 webapi.sporttery.cn API）。

    API 端点: https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry
    支持5种玩法: had(胜平负), hhad(让球), crs(比分), ttg(总进球), hafu(半全场)
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
        params = {"matchInfo": "1", "poolCode": pool_code, "beginDate": begin_date, "endDate": end_date}
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
        """获取所有5种玩法赔率，按 matchId 聚合"""
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
                            "matchId": mid, "matchNumStr": m.get("matchNumStr", ""),
                            "matchNum": m.get("matchNum", 0), "matchDate": m.get("matchDate", ""),
                            "matchTime": m.get("matchTime", ""), "matchStatus": m.get("matchStatus", ""),
                            "leagueAbbName": m.get("leagueAbbName", ""), "leagueAllName": m.get("leagueAllName", ""),
                            "homeTeamAbbName": m.get("homeTeamAbbName", ""), "homeTeamAllName": m.get("homeTeamAllName", ""),
                            "awayTeamAbbName": m.get("awayTeamAbbName", ""), "awayTeamAllName": m.get("awayTeamAllName", ""),
                        }
                    matches[mid][pool_code] = m.get(pool_code, {})
        return matches

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        """单场比赛查询 — 通过批量获取后按队名匹配"""
        snapshots = self.fetch_batch([match])
        return snapshots[0] if snapshots else None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        """批量获取竞彩赔率"""
        if not matches:
            return []

        today = datetime.now().strftime("%Y-%m-%d")
        max_date = today
        for m in matches:
            if m.kickoff_at:
                d = m.kickoff_at.strftime("%Y-%m-%d")
                if d > max_date:
                    max_date = d
        end_date = (datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

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

        handicap = 0
        goal_line = hhad.get("goalLine", "")
        if goal_line:
            try:
                handicap = int(float(goal_line))
            except (ValueError, TypeError):
                pass

        multi_pool_odds = {
            "had": {"h": had.get("h"), "d": had.get("d"), "a": had.get("a"),
                    "updateDate": had.get("updateDate", ""), "updateTime": had.get("updateTime", "")},
            "hhad": {"h": hhad.get("h"), "d": hhad.get("d"), "a": hhad.get("a"),
                     "goalLine": hhad.get("goalLine", ""),
                     "updateDate": hhad.get("updateDate", ""), "updateTime": hhad.get("updateTime", "")},
            "crs": best_match.get("crs", {}),
            "ttg": best_match.get("ttg", {}),
            "hafu": best_match.get("hafu", {}),
        }

        return OddsSnapshot(
            match_id=match.id, source="jingcai",
            odds_home=odds_home, odds_draw=odds_draw, odds_away=odds_away,
            recorded_at=datetime.now(timezone.utc),
            handicap=float(handicap), multi_pool_odds=multi_pool_odds,
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
