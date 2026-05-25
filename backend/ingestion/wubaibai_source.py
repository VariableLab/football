"""
500.com 百家欧赔采集源

采集策略:
  1. 从 live.500.com/wanchang.php?e=YYYY-MM-DD 获取当日比赛列表
  2. 从比赛列表中提取 match_id（从"欧"链接中解析 ouzhi-MATCHID.shtml）
  3. 按需访问 odds.500.com/fenxi/ouzhi-MATCHID.shtml 获取百家欧赔明细
  4. 解析赔率表格，提取所有博彩公司的即时胜/平/负，取平均值返回

特点:
  - 每场覆盖 20+ 博彩公司，含竞彩官方/澳门/香港马会/威廉希尔/bet365 等
  - 免费、无 API key、无请求限制
  - 比赛列表有 30 分钟缓存，单场赔率有 5 分钟缓存
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from data_source.base import OddsSnapshot, OddsSource
from ingestion.odds_collector import _fetch_with_retry

logger = logging.getLogger("500")

# ════════════════════════════════════════
# 队名标准化映射（复用 zgzcw 映射 + 500.com 特有）
# ════════════════════════════════════════

TEAM_NAME_ALIAS = {
    "曼联": "曼联", "曼城": "曼城", "阿森纳": "阿森纳", "利物浦": "利物浦",
    "切尔西": "切尔西", "热刺": "热刺", "纽卡斯尔": "纽卡斯尔", "纽卡斯尔联": "纽卡斯尔",
    "阿斯顿维拉": "阿斯顿维拉", "富勒姆": "富勒姆", "布伦特福德": "布伦特福德",
    "水晶宫": "水晶宫", "狼队": "狼队", "莱切斯特城": "莱切斯特", "莱斯特城": "莱切斯特",
    "南安普敦": "南安普敦", "利兹联": "利兹联", "利兹": "利兹联",
    "诺丁汉森林": "诺丁汉森林", "布莱顿": "布莱顿", "西汉姆联": "西汉姆",
    "西汉姆": "西汉姆", "伯恩茅斯": "伯恩茅斯", "埃弗顿": "埃弗顿",
    "伊普斯维奇": "伊普斯维奇", "米德尔斯堡": "米德尔斯堡",
    "巴塞罗那": "巴塞罗那", "皇家马德里": "皇马", "皇马": "皇马",
    "马德里竞技": "马竞", "马竞": "马竞", "毕尔巴鄂竞技": "毕尔巴鄂",
    "毕尔巴鄂": "毕尔巴鄂", "皇家社会": "皇家社会", "皇家贝蒂斯": "皇家贝蒂斯",
    "贝蒂斯": "贝蒂斯",
    "塞维利亚": "塞维利亚", "巴伦西亚": "巴伦西亚", "巴列卡诺": "巴列卡诺",
    "赫塔费": "赫塔费", "赫塔菲": "赫塔费", "赫罗纳": "赫罗纳",
    "西班牙人": "西班牙人", "奥萨苏纳": "奥萨苏纳", "马洛卡": "马洛卡",
    "维戈塞尔塔": "塞尔塔", "塞尔塔": "塞尔塔",
    "巴拉多利德": "巴拉多利德", "莱加内斯": "莱加内斯",
    "比利亚雷亚尔": "比利亚雷亚尔", "莱万特": "莱万特", "埃尔切": "埃尔切",
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
    "利雅得胜利": "利雅胜利", "利雅得新月": "利雅新月",
    "利雅胜利": "利雅胜利", "利雅新月": "利雅新月",
    # 韩K
    "仁川联合": "仁川联", "仁川联": "仁川联",
    "浦项铁人": "浦项制铁", "浦项制铁": "浦项制铁",
    "光州FC": "光州FC", "FC首尔": "首尔FC", "首尔FC": "首尔FC",
    # 葡超
    "里奥阿维": "里奥阿维", "阿马多拉": "阿马多拉", "法马利康": "法马利康",
    # 英冠
    "米尔沃尔": "米尔沃尔", "赫尔城": "赫尔城", "米堡": "米堡",
    # 瑞典超
    "天狼星": "天狼星", "奥尔格里": "奥尔格里",
    # 法乙
    "圣旺红星": "圣旺红星", "红星": "圣旺红星", "罗德兹": "罗德兹",
    # 沙特联补充
    "阿尔科鲁德": "阿尔科鲁德", "阿科多": "阿科多",
}


def _normalise_team_name(raw: str) -> str:
    name = raw.strip()
    # 去掉排名前缀如 [08]
    name = re.sub(r'^\[\d+\]', '', name)
    # 去掉排名后缀如 [12]
    name = re.sub(r'\[\d+\]$', '', name)
    name = name.strip()
    return TEAM_NAME_ALIAS.get(name, name)


def _safe_float(val: Any) -> Optional[float]:
    try:
        v = float(str(val).strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ════════════════════════════════════════
# WubaibaiOddsSource
# ════════════════════════════════════════

class WubaibaiOddsSource(OddsSource):
    """
    500.com 百家欧赔采集。

    从 live.500.com 获取比赛列表，按需访问 odds.500.com 获取每家赔率明细。
    纯 HTTP + BeautifulSoup，无需 JS 渲染。
    """
    name = "500"
    MATCH_LIST_URL = "https://live.500.com/wanchang.php"
    ODDS_URL_TMPL = "https://odds.500.com/fenxi/ouzhi-{match_id}.shtml"

    def __init__(self):
        # 500.com SSL 证书配置错误（hostname mismatch + handshake failure）
        # odds.500.com 和 live.500.com 使用不同证书，Python 默认验证失败
        # 使用 verify=False 绕过验证，仅读取公开赔率数据，无安全风险
        self.client = httpx.Client(
            timeout=20.0,
            verify=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://live.500.com/",
            },
            follow_redirects=True,
        )
        self._match_index: Dict[int, Dict] = {}
        self._index_fetched_at: Optional[datetime] = None
        self._index_ttl = timedelta(minutes=30)
        self._odds_page_cache: Dict[int, Dict] = {}
        self._odds_page_ttl = timedelta(minutes=5)

    # ─── 比赛列表索引 ──────────────────────

    def _fetch_match_index(self, force: bool = False,
                           date_str: str = None) -> Dict[int, Dict]:
        """从 live.500.com/wanchang.php?e=YYYY-MM-DD 获取比赛列表"""
        now = datetime.now()
        if (
            not force
            and self._index_fetched_at
            and (now - self._index_fetched_at) < self._index_ttl
            and self._match_index
        ):
            return self._match_index

        if date_str is None:
            date_str = now.strftime("%Y-%m-%d")

        url = f"{self.MATCH_LIST_URL}?e={date_str}"
        try:
            resp = _fetch_with_retry(self.client, url)
            resp.raise_for_status()
            # 500.com uses gb2312 encoding, decode manually
            html = resp.content.decode("gb2312", errors="replace")
            index = self._parse_match_list(html)
            self._match_index = index
            self._index_fetched_at = now
            logger.info(f"[500] index refreshed: {len(index)} matches for {date_str}")
        except Exception as e:
            logger.warning(f"[500] index fetch failed: {e}")

        return self._match_index

    def _parse_match_list(self, html: str) -> Dict[int, Dict]:
        """解析比赛列表 HTML，从 tr 的 gy 属性提取队名"""
        soup = BeautifulSoup(html, "html.parser")
        index: Dict[int, Dict] = {}

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            m = re.search(r'ouzhi-(\d+)\.s?html?', href)
            if not m:
                continue
            match_id = int(m.group(1))
            if match_id in index:
                continue

            row = a_tag.find_parent("tr")
            if not row:
                continue

            # 优先从 gy 属性提取: "联赛名,主队,客队"
            gy = row.get("gy", "")
            home_name = ""
            away_name = ""
            league = ""
            if gy:
                parts = gy.split(",")
                if len(parts) >= 3:
                    league = parts[0].strip()
                    home_name = _normalise_team_name(parts[1].strip())
                    away_name = _normalise_team_name(parts[2].strip())

            # fallback: from team links in cells
            if not home_name or not away_name:
                team_links = row.select("a[href*='/team/']")
                names = []
                for link in team_links:
                    t = link.text.strip()
                    if t and len(t) >= 2:
                        names.append(_normalise_team_name(t))
                if len(names) >= 2:
                    home_name, away_name = names[0], names[1]

            if not home_name or not away_name:
                continue

            # 提取比赛时间
            match_time = ""
            for cell in row.find_all("td"):
                text = cell.get_text(" ", strip=True)
                if re.match(r'\d{2}-\d{2}\s+\d{2}:\d{2}', text):
                    match_time = text
                    break

            index[match_id] = {
                "home": home_name,
                "away": away_name,
                "time": match_time,
                "league": league,
            }

        return index

    def _find_match_entry(self, home_name: str, away_name: str) -> Optional[Dict]:
        """在索引中按队名查找比赛"""
        index = self._fetch_match_index()
        home_norm = _normalise_team_name(home_name)
        away_norm = _normalise_team_name(away_name)

        for match_id, info in index.items():
            if info["home"] == home_norm and info["away"] == away_norm:
                return info
        for info in index.values():
            ih, ia = info["home"], info["away"]
            if (home_norm in ih or ih in home_norm) and (
                away_norm in ia or ia in away_norm
            ):
                return info
        return None

    # ─── 百家欧赔详情页解析 ────────────────

    def _fetch_odds_page(self, match_id: int, home_hint: str = "",
                           away_hint: str = "") -> Optional[Dict]:
        """
        获取并解析百家欧赔详情页。
        返回 {"home": 平均主胜赔率, "draw": ..., "away": ..., "bookmakers": [...]}
        """
        now = datetime.now()
        cached = self._odds_page_cache.get(match_id)
        if cached and (now - cached.get("_ts", now)) < self._odds_page_ttl:
            return cached.get("data")

        url = self.ODDS_URL_TMPL.format(match_id=match_id)
        try:
            resp = _fetch_with_retry(self.client, url)
            resp.raise_for_status()
            # 500.com odds page also uses gb2312
            html = resp.content.decode("gb2312", errors="replace")
            data = self._parse_odds_table(html, match_id, home_hint, away_hint)
            if data:
                self._odds_page_cache[match_id] = {"_ts": now, "data": data}
            return data
        except Exception as e:
            logger.warning(f"[500] odds page fetch failed for {match_id}: {e}")
            return None

    def _parse_odds_table(self, html: str, match_id: int,
                          home_hint: str = "", away_hint: str = "") -> Optional[Dict]:
        """解析百家欧赔表格，从 id=datatb 的大表中提取所有公司的即时赔率"""
        soup = BeautifulSoup(html, "html.parser")

        # 解析队名 —— 优先使用 index 提供的提示
        home_name = home_hint
        away_name = away_hint

        # 方案1: VS 模式
        for elem in soup.find_all(text=re.compile(r'\bVS\b', re.IGNORECASE)):
            parent = elem.find_parent()
            if parent:
                text = parent.get_text(" ", strip=True)
                m = re.search(r'(.+?)\s*VS\s*(.+)', text, re.IGNORECASE)
                if m:
                    home_name = _normalise_team_name(m.group(1).split()[-1])
                    away_name = _normalise_team_name(m.group(2).split()[0])
                    break

        # 方案2: team/ 链接
        if not home_name:
            team_links = soup.select("a[href*='/team/']")
            names = []
            for link in team_links[:3]:
                t = link.text.strip()
                if t and len(t) >= 2 and len(t) <= 20:
                    n = _normalise_team_name(t)
                    if n not in names:
                        names.append(n)
            if len(names) >= 2:
                home_name, away_name = names[0], names[1]

        # 解析赔率表格 —— 定位 id=datatb
        odds_table = soup.find("table", id="datatb")
        if not odds_table:
            odds_table = soup.find("table")
        if not odds_table:
            logger.warning(f"[500] no odds table found for match {match_id}")
            return None

        all_home_odds: List[float] = []
        all_draw_odds: List[float] = []
        all_away_odds: List[float] = []
        bookmakers: List[Dict] = []

        for row in odds_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # 博彩公司行通常有 27 个 td
            bm_name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            if not bm_name or len(bm_name) < 2:
                continue
            # 排除非赔率行
            if bm_name in ("赔率公司", "筛选", "平均值"):
                continue

            odds_h = _safe_float(cells[3].get_text()) if len(cells) > 3 else None
            odds_d = _safe_float(cells[4].get_text()) if len(cells) > 4 else None
            odds_a = _safe_float(cells[5].get_text()) if len(cells) > 5 else None

            if not (odds_h and odds_d and odds_a):
                continue
            if odds_h < 1.01 or odds_h > 30:
                continue

            all_home_odds.append(odds_h)
            all_draw_odds.append(odds_d)
            all_away_odds.append(odds_a)
            bookmakers.append({
                "name": bm_name,
                "home": odds_h,
                "draw": odds_d,
                "away": odds_a,
            })

        if len(all_home_odds) < 3:
            logger.warning(f"[500] only {len(all_home_odds)} bookmakers for match {match_id}")
            return None

        avg_home = round(sum(all_home_odds) / len(all_home_odds), 2)
        avg_draw = round(sum(all_draw_odds) / len(all_draw_odds), 2)
        avg_away = round(sum(all_away_odds) / len(all_away_odds), 2)

        logger.info(
            f"[500] match {match_id} ({home_name} vs {away_name}): "
            f"{avg_home}/{avg_draw}/{avg_away} "
            f"(avg of {len(bookmakers)} bookmakers)"
        )

        return {
            "home": avg_home,
            "draw": avg_draw,
            "away": avg_away,
            "home_name": home_name,
            "away_name": away_name,
            "bookmaker_count": len(bookmakers),
            "bookmakers": bookmakers,
        }

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

        match_id = None
        for mid, info in self._match_index.items():
            if info["home"] == entry["home"] and info["away"] == entry["away"]:
                match_id = mid
                break

        if not match_id:
            return None

        odds_data = self._fetch_odds_page(match_id, entry["home"], entry["away"])
        if not odds_data:
            return None

        bm_list = odds_data.get("bookmakers", [])
        multi_pool_odds = {
            "source": "500.com",
            "match_id_500": match_id,
            "bookmaker_count": odds_data.get("bookmaker_count", 0),
            "average": {
                "home": odds_data["home"],
                "draw": odds_data["draw"],
                "away": odds_data["away"],
            },
            "bookmakers": {
                bm["name"]: {"home": bm["home"], "draw": bm["draw"], "away": bm["away"]}
                for bm in bm_list
            },
        }

        return OddsSnapshot(
            match_id=match.id,
            source=self.name,
            odds_home=odds_data["home"],
            odds_draw=odds_data["draw"],
            odds_away=odds_data["away"],
            recorded_at=datetime.now(timezone.utc),
            multi_pool_odds=multi_pool_odds,
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
                logger.warning(f"[500] batch fail {match.match_code}: {e}")
        logger.info(f"[500] batch: {len(results)}/{len(matches)} matches")
        return results

    def close(self):
        self.client.close()


# ════════════════════════════════════════
# 便捷函数（供 scheduler 调用）
# ════════════════════════════════════════

def collect_500_odds(db: Session) -> Dict:
    """
    从 500.com 采集百家欧赔并更新数据库。
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

    source = WubaibaiOddsSource()
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
            match.odds_source = "500"

            # Dedup: skip if already have a snapshot within 5min
            cutoff = snap.recorded_at - timedelta(minutes=5)
            exists = db.query(OddsHistory).filter(
                OddsHistory.match_id == snap.match_id,
                OddsHistory.source == "500",
                OddsHistory.recorded_at >= cutoff,
            ).first()
            if not exists:
                db.add(
                    OddsHistory(
                        match_id=snap.match_id,
                        source="500",
                        odds_home=snap.odds_home,
                        odds_draw=snap.odds_draw,
                        odds_away=snap.odds_away,
                        recorded_at=snap.recorded_at,
                        is_real=True,
                    )
                )
            updated += 1

        db.commit()
        logger.info(f"[500-job] updated {updated}/{len(upcoming)} matches")
        return {"matches": len(upcoming), "updated": updated}
    finally:
        source.close()