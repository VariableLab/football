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
from odds_collector import _fetch_with_retry

logger = logging.getLogger("zgzcw")

# ════════════════════════════════════════
# 队名标准化映射
# ════════════════════════════════════════

TEAM_NAME_ALIAS = {
    "曼联": "曼联", "曼城": "曼城", "阿森纳": "阿森纳", "利物浦": "利物浦",
    "切尔西": "切尔西", "热刺": "热刺", "纽卡斯尔": "纽卡斯尔", "纽卡斯尔联": "纽卡斯尔",
    "阿斯顿维拉": "阿斯顿维拉", "富勒姆": "富勒姆", "布伦特福德": "布伦特福德",
    "水晶宫": "水晶宫", "狼队": "狼队", "莱切斯特城": "莱切斯特", "莱斯特城": "莱切斯特",
    "南安普敦": "南安普敦", "利兹联": "利兹联", "利兹": "利兹联",
    "诺丁汉森林": "诺丁汉森林", "布莱顿": "布莱顿", "西汉姆联": "西汉姆",
    "西汉姆": "西汉姆", "伯恩茅斯": "伯恩茅斯", "埃弗顿": "埃弗顿",
    "伊普斯维奇": "伊普斯维奇",
    "巴塞罗那": "巴塞罗那", "皇家马德里": "皇马", "皇马": "皇马",
    "马德里竞技": "马竞", "马竞": "马竞", "毕尔巴鄂竞技": "毕尔巴鄂",
    "毕尔巴鄂": "毕尔巴鄂", "皇家社会": "皇家社会", "皇家贝蒂斯": "皇家贝蒂斯",
    "塞维利亚": "塞维利亚", "巴伦西亚": "巴伦西亚", "巴列卡诺": "巴列卡诺",
    "赫塔费": "赫塔费", "赫塔菲": "赫塔费", "赫罗纳": "赫罗纳",
    "西班牙人": "西班牙人", "奥萨苏纳": "奥萨苏纳", "马洛卡": "马洛卡",
    "塞尔塔": "塞尔塔", "巴拉多利德": "巴拉多利德", "莱加内斯": "莱加内斯",
    "比利亚雷亚尔": "比利亚雷亚尔", "贝蒂斯": "贝蒂斯", "莱万特": "莱万特",
    "埃尔切": "埃尔切",
    "国际米兰": "国际米兰", "AC米兰": "AC米兰", "尤文图斯": "尤文图斯",
    "那不勒斯": "那不勒斯", "亚特兰大": "亚特兰大", "罗马": "罗马",
    "拉齐奥": "拉齐奥", "博洛尼亚": "博洛尼亚", "佛罗伦萨": "佛罗伦萨",
    "都灵": "都灵", "乌迪内斯": "乌迪内斯", "热那亚": "热那亚",
    "帕尔马": "帕尔马", "卡利亚里": "卡利亚里", "科莫": "科莫",
    "恩波利": "恩波利", "莱切": "莱切", "维罗纳": "维罗纳",
    "蒙扎": "蒙扎", "威尼斯": "威尼斯",
    "拜仁慕尼黑": "拜仁", "拜仁": "拜仁", "多特蒙德": "多特蒙德",
    "勒沃库森": "勒沃库森", "莱比锡": "莱比锡", "RB莱比锡": "莱比锡",
    "斯图加特": "斯图加特", "法兰克福": "法兰克福", "沃尔夫斯堡": "沃尔夫斯堡",
    "弗赖堡": "弗赖堡", "门兴格拉德巴赫": "门兴", "门兴": "门兴",
    "柏林联合": "柏林联合", "霍芬海姆": "霍芬海姆", "美因茨": "美因茨",
    "奥格斯堡": "奥格斯堡", "云达不来梅": "不莱梅", "不莱梅": "不莱梅",
    "波鸿": "波鸿", "圣保利": "圣保利", "海登海姆": "海登海姆",
    "巴黎圣日尔曼": "巴黎", "巴黎圣日耳曼": "巴黎", "马赛": "马赛",
    "里昂": "里昂", "摩纳哥": "摩纳哥", "里尔": "里尔", "朗斯": "朗斯",
    "雷恩": "雷恩", "尼斯": "尼斯", "斯特拉斯堡": "斯特拉斯堡",
    "图卢兹": "图卢兹", "南特": "南特", "布雷斯特": "布雷斯特",
    "蒙彼利埃": "蒙彼利埃", "圣埃蒂安": "圣埃蒂安", "欧塞尔": "欧塞尔",
    "昂热": "昂热", "兰斯": "兰斯", "勒阿弗尔": "勒阿弗尔",
    "本菲卡": "本菲卡", "波尔图": "波尔图", "里斯本竞技": "里斯本",
    "里斯本": "里斯本", "布拉加": "布拉加", "吉马良斯": "吉马良斯",
    "阿贾克斯": "阿贾克斯", "PSV埃因霍温": "埃因霍温", "埃因霍温": "埃因霍温",
    "费耶诺德": "费耶诺德", "阿尔克马尔": "阿尔克马", "阿尔克马": "阿尔克马",
    "特温特": "特温特",
    # 沙特联赛
    "利雅胜利": "利雅胜利", "利雅新月": "利雅新月",
    "未来体育": "未来体育", "利雅青年": "利雅青年",
    "布赖代": "布赖代", "吉达国民": "吉达国民",
    # 葡超
    "里奥阿维": "里奥阿维", "阿马多拉": "阿马多拉", "法马利康": "法马利康",
    # 英冠
    "米尔沃尔": "米尔沃尔", "赫尔城": "赫尔城", "米堡": "米堡",
    # 瑞典超
    "天狼星": "天狼星", "奥尔格里": "奥尔格里",
    # 韩K
    "仁川联队": "仁川联", "浦项制铁": "浦项制铁",
    "光州FC": "光州FC", "首尔FC": "首尔FC",
    # 法乙
    "圣旺红星": "圣旺红星", "罗德兹": "罗德兹",
}


def _normalise_team_name(raw: str) -> str:
    name = raw.strip()
    return TEAM_NAME_ALIAS.get(name, name)


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
    from models import Match as MatchModel, OddsHistory

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