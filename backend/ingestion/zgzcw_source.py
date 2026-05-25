"""
中国足彩网（zgzcw.com）赔率采集源

采集策略：
  1. 从 live.zgzcw.com 获取当日比赛列表（含 match_id、中文队名、赔率）
  2. live.zgzcw.com 页面直接包含结构化赔率数据：
     - div.oupei > span × 3  → 欧赔（home / draw / away）
     - div.yapan > span × 3  → 亚盘（home_rate / handicap / away_rate）
     - div.jcsp > span × 3   → 竞彩胜平负SP
     - div.jcrqsp > span × 3 → 竞彩让球胜平负SP
  3. 欧赔作为主赔率返回 OddsSnapshot，各家详情存入 multi_pool_odds

特点：
  - 纯 HTML 解析，无需 JS / headless 浏览器
  - 一场请求同时获取欧赔+亚盘+竞彩SP
  - 免费、无 API key、无请求次数限制
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from data_source.base import OddsSnapshot, OddsSource
from ingestion.odds_collector import _fetch_with_retry
from data_cleaner import resolve_team_name

logger = logging.getLogger("zgzcw")

# ════════════════════════════════════════
# 队名标准化映射 (已迁移至 data_cleaner + YAML)
# ════════════════════════════════════════

def _normalise_team_name(raw: str) -> str:
    return resolve_team_name(raw)



# ════════════════════════════════════════
# ZgzcwOddsSource
# ════════════════════════════════════════

class ZgzcwOddsSource(OddsSource):
    """
    中国足彩网（zgzcw.com）赔率采集。

    直接从 live.zgzcw.com 解析欧赔/亚盘/竞彩SP 数据。
    无需 JS 渲染，纯 HTTP + BeautifulSoup 即可。
    """
    name = "zgzcw"
    LIVE_URL = "https://live.zgzcw.com/"

    def __init__(self):
        self.client = httpx.Client(
            timeout=20.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )
        self._match_index: Dict[int, Dict] = {}
        self._index_fetched_at: Optional[datetime] = None
        self._index_ttl = timedelta(minutes=30)

    # ─── 比赛索引 & 赔率解析 ──────────────

    def _fetch_match_index(self, force: bool = False) -> Dict[int, Dict]:
        """从 live.zgzcw.com 获取当日比赛列表，含赔率数据"""
        now = datetime.now()
        if (
            not force
            and self._index_fetched_at
            and (now - self._index_fetched_at) < self._index_ttl
            and self._match_index
        ):
            return self._match_index

        try:
            resp = _fetch_with_retry(self.client, self.LIVE_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            index: Dict[int, Dict] = {}
            for row in soup.select("tr.matchTr"):
                match_id_str = row.get("matchid", "")
                if not match_id_str:
                    continue
                try:
                    match_id = int(match_id_str)
                except ValueError:
                    continue

                home_el = row.select_one(".sptr a")
                away_el = row.select_one(".sptl a")
                league_el = row.select_one(".matchType a")
                date_el = row.select_one(".matchDate")
                status_el = row.select_one(".matchStatus strong")
                odd_td = row.select_one("td.oddMatch")

                home_name = home_el.text.strip() if home_el else ""
                away_name = away_el.text.strip() if away_el else ""
                league = league_el.text.strip() if league_el else ""
                match_date = date_el.get("date", "") if date_el else ""
                match_status = status_el.text.strip() if status_el else ""

                if not home_name or not away_name:
                    continue

                info = {
                    "home": _normalise_team_name(home_name),
                    "away": _normalise_team_name(away_name),
                    "league": league,
                    "time": match_date,
                    "status": match_status,
                }

                # 解析赔率
                if odd_td:
                    odds = self._parse_odd_match_td(odd_td)
                    if odds:
                        info.update(odds)

                index[match_id] = info

            self._match_index = index
            self._index_fetched_at = now
            logger.info(f"[zgzcw] index refreshed: {len(index)} matches")

        except Exception as e:
            logger.warning(f"[zgzcw] index fetch failed: {e}")

        return self._match_index

    @staticmethod
    def _parse_odd_match_td(td) -> Optional[Dict]:
        """解析 td.oddMatch 中的结构化赔率"""
        result: Dict[str, Any] = {}

        oupei_div = td.select_one("div.oupei")
        if oupei_div:
            spans = oupei_div.find_all("span")
            if len(spans) >= 3:
                result["odds_home"] = _safe_float(spans[0].text)
                result["odds_draw"] = _safe_float(spans[1].text)
                result["odds_away"] = _safe_float(spans[2].text)

        yapan_div = td.select_one("div.yapan")
        if yapan_div:
            spans = yapan_div.find_all("span")
            if len(spans) >= 3:
                result["ah_home"] = _safe_float(spans[0].text)
                result["ah_handicap"] = _safe_float(spans[1].text)
                result["ah_away"] = _safe_float(spans[2].text)

        jcsp_div = td.select_one("div.jcsp")
        if jcsp_div:
            spans = jcsp_div.find_all("span")
            if len(spans) >= 3:
                result["jcsp_home"] = _safe_float(spans[0].text)
                result["jcsp_draw"] = _safe_float(spans[1].text)
                result["jcsp_away"] = _safe_float(spans[2].text)

        jcrq_div = td.select_one("div.jcrqsp")
        if jcrq_div:
            spans = jcrq_div.find_all("span")
            if len(spans) >= 3:
                result["jcrq_home"] = _safe_float(spans[0].text)
                result["jcrq_draw"] = _safe_float(spans[1].text)
                result["jcrq_away"] = _safe_float(spans[2].text)

        if not result.get("odds_home"):
            return None
        return result

    def _find_match_entry(self, home_name: str, away_name: str) -> Optional[Dict]:
        """在索引中按队名查找比赛"""
        index = self._fetch_match_index()
        home_norm = _normalise_team_name(home_name)
        away_norm = _normalise_team_name(away_name)

        for info in index.values():
            if info["home"] == home_norm and info["away"] == away_norm:
                return info
        for info in index.values():
            ih, ia = info["home"], info["away"]
            if (home_norm in ih or ih in home_norm) and (
                away_norm in ia or ia in away_norm
            ):
                return info
        return None

    # ─── OddsSource 接口 ───────────────────

    def fetch(self, match) -> Optional[OddsSnapshot]:
        home = match.home_team.name if match.home_team else ""
        away = match.away_team.name if match.away_team else ""
        if not home or not away:
            return None

        self._fetch_match_index()
        entry = self._find_match_entry(home, away)
        if not entry:
            return None

        odds_h = entry.get("odds_home")
        if not odds_h:
            return None

        return OddsSnapshot(
            match_id=match.id,
            source=self.name,
            odds_home=odds_h,
            odds_draw=entry.get("odds_draw"),
            odds_away=entry.get("odds_away"),
            recorded_at=datetime.now(timezone.utc),
            multi_pool_odds={
                "source": "zgzcw.com live",
                "oupei": {
                    "home": odds_h,
                    "draw": entry.get("odds_draw"),
                    "away": entry.get("odds_away"),
                },
                "yapan": {
                    "home": entry.get("ah_home"),
                    "handicap": entry.get("ah_handicap"),
                    "away": entry.get("ah_away"),
                },
                "jingcai_sp": {
                    "home": entry.get("jcsp_home"),
                    "draw": entry.get("jcsp_draw"),
                    "away": entry.get("jcsp_away"),
                },
                "jingcai_rqsp": {
                    "home": entry.get("jcrq_home"),
                    "draw": entry.get("jcrq_draw"),
                    "away": entry.get("jcrq_away"),
                },
            },
        )

    def fetch_batch(self, matches) -> List[OddsSnapshot]:
        self._fetch_match_index()
        results: List[OddsSnapshot] = []
        for match in matches:
            try:
                snap = self.fetch(match)
                if snap:
                    results.append(snap)
            except Exception as e:
                logger.warning(f"[zgzcw] batch fail {match.match_code}: {e}")
        logger.info(f"[zgzcw] batch: {len(results)}/{len(matches)} matches")
        return results

    def close(self):
        self.client.close()


# ════════════════════════════════════════
# 便捷函数
# ════════════════════════════════════════

def _safe_float(val: Any) -> Optional[float]:
    try:
        v = float(str(val).strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def collect_zgzcw_odds(db: Session) -> Dict:
    """
    从 zgzcw.com 采集赔率并更新数据库。
    供 scheduler 定时调用。
    """
    from database.models import Match as MatchModel, OddsHistory

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=72)
    upcoming = (
        db.query(MatchModel)
        .filter(MatchModel.kickoff_at.between(now, window_end))
        .all()
    )

    if not upcoming:
        return {"matches": 0, "updated": 0}

    source = ZgzcwOddsSource()
    try:
        snapshots = source.fetch_batch(upcoming)
        updated = 0
        for snap in snapshots:
            match = next((m for m in upcoming if m.id == snap.match_id), None)
            if not match:
                continue

            match.odds_home = snap.odds_home
            match.odds_draw = snap.odds_draw
            match.odds_away = snap.odds_away
            match.odds_source = "zgzcw"

            # Dedup: skip if already have a snapshot within 5min
            cutoff = snap.recorded_at - timedelta(minutes=5)
            exists = db.query(OddsHistory).filter(
                OddsHistory.match_id == snap.match_id,
                OddsHistory.source == "zgzcw",
                OddsHistory.recorded_at >= cutoff,
            ).first()
            if not exists:
                db.add(
                    OddsHistory(
                        match_id=snap.match_id,
                        source="zgzcw",
                        odds_home=snap.odds_home,
                        odds_draw=snap.odds_draw,
                        odds_away=snap.odds_away,
                        recorded_at=snap.recorded_at,
                        is_real=True,
                    )
                )
            updated += 1

        db.commit()
        logger.info(f"[zgzcw-job] updated {updated}/{len(upcoming)} matches")
        return {"matches": len(upcoming), "updated": updated}
    finally:
        source.close()