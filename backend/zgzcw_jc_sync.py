"""
zgzcw_jc_sync.py — 从 zgzcw 同步竞彩比赛到数据库

数据源: https://live.zgzcw.com/
特点:
  - 纯 HTML 解析，无需 JS 渲染
  - 包含竞彩 SP 值（jcsp_home/draw/away）
  - 包含让球 SP 值（jcrq_home/draw/away）
  - 覆盖欧洲五大联赛 + 日韩主要联赛

流程:
  1. 从 live.zgzcw.com 获取比赛列表
  2. 筛选包含竞彩 SP 值的比赛
  3. 按队名查找/创建 Team 记录
  4. 按比赛编码查找/创建 Match 记录
  5. 写入赔率数据
"""

import httpx
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from logger import get_logger

logger = get_logger("zgzcw_jc_sync")

# 队名映射（zgzcw 中文 -> 数据库英文）
TEAM_NAME_MAP = {
    # 英超
    "曼联": "Manchester United", "诺丁汉": "Nott'm Forest",
    "狼队": "Wolves", "富勒姆": "Fulham",
    "埃弗顿": "Everton", "桑德兰": "Sunderland",
    "布伦特": "Brentford", "水晶宫": "Crystal Palace",
    "纽卡斯尔": "Newcastle", "西汉姆联": "West Ham",
    # 意甲
    "热那亚": "Genoa", "AC米兰": "AC Milan",
    "尤文图斯": "Juventus", "佛罗伦萨": "Fiorentina",
    "罗马": "Roma", "拉齐奥": "Lazio",
    "科莫": "Como", "帕尔马": "Parma",
    "亚特兰大": "Atalanta", "博洛尼亚": "Bologna",
    "萨索洛": "Sassuolo", "莱切": "Lecce",
    "乌迪内斯": "Udinese", "克雷莫纳": "Cremonese",
    # 西甲
    "奥萨苏纳": "Osasuna", "西班牙人": "Espanyol",
    "毕尔巴鄂": "Athletic Bilbao", "塞尔塔": "Celta",
    "奥维耶多": "Real Oviedo", "阿拉维斯": "Deportivo Alavés",
    "马竞": "Atletico Madrid", "赫罗纳": "Girona",
    "莱万特": "Levante UD", "马洛卡": "Mallorca",
    "巴列卡诺": "Rayo Vallecano", "比利亚雷": "Villarreal CF",
    "埃尔切": "Elche CF", "赫塔菲": "Getafe CF",
    "塞维利亚": "Sevilla", "皇马": "Real Madrid",
    "皇家社会": "Real Sociedad", "巴伦西亚": "Valencia CF",
    "巴塞罗那": "Barcelona", "贝蒂斯": "Real Betis",
    # 德甲/德乙
    "达姆施塔": "Darmstadt", "帕德博恩": "Paderborn",
    # 法甲
    "里昂": "Lyon", "朗斯": "Lens",
    "里尔": "Lille", "欧塞尔": "Auxerre",
    "马赛": "Marseille", "雷恩": "Rennes",
    # 荷甲
    "福伦丹": "Volendam", "特尔斯达": "Telstar",
    "海伦芬": "Heerenveen", "阿贾克斯": "Ajax",
    # 瑞典超
    "哈马比": "Hammarby", "马尔默": "Malmo",
    # 日职联
    "大阪樱花": "Cerezo Osaka", "名古屋鲸": "Nagoya Grampus",
    "川崎前锋": "Kawasaki Frontale", "町田泽维": "Machida Zelvia",
    # 韩K联
    "全北现代": "Jeonbuk Motors", "金泉尚武": "Gimcheon Sangmu",
    "富川FC": "Bucheon FC", "浦项制铁": "Pohang Steelers",
    # 美职联
    "纳什威尔": "Nashville SC", "洛杉矶": "LA Galaxy",
}

# 联赛映射（zgzcw -> 数据库）
LEAGUE_MAP = {
    "英超": "EPL",
    "意甲": "SerieA",
    "西甲": "LaLiga",
    "德甲": "Bundesliga",
    "法甲": "Ligue1",
    "荷甲": "Eredivisie",
    "德乙": "2. Bundesliga",
    "瑞典超": "Allsvenskan",
    "日职联": "J1 League",
    "韩K联": "K League 1",
    "美职联": "MLS",
}


def _normalise_team_name(zgzcw_name: str) -> str:
    """将 zgzcw 队名转换为数据库队名"""
    return TEAM_NAME_MAP.get(zgzcw_name, zgzcw_name)


def _normalise_league(zgzcw_league: str) -> str:
    """将 zgzcw 联赛转换为数据库联赛"""
    return LEAGUE_MAP.get(zgzcw_league, zgzcw_league)


def fetch_jc_matches() -> List[Dict]:
    """从 live.zgzcw.com 获取竞彩比赛列表"""
    url = "https://live.zgzcw.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        matches = []
        for row in soup.select("tr.matchTr"):
            match_id = row.get("matchid", "")
            home_el = row.select_one(".sptr a")
            away_el = row.select_one(".sptl a")
            league_el = row.select_one(".matchType a")
            date_el = row.select_one(".matchDate")
            jcsp = row.select(".jcsp span")
            jcrq = row.select(".jcrqsp span")

            home = home_el.get_text(strip=True) if home_el else ""
            away = away_el.get_text(strip=True) if away_el else ""
            league = league_el.get_text(strip=True) if league_el else ""
            date = date_el.get("date", "") if date_el else ""

            # 只保留有竞彩 SP 值的比赛
            if not (jcsp and len(jcsp) >= 3):
                continue

            sp_home = jcsp[0].get_text(strip=True)
            sp_draw = jcsp[1].get_text(strip=True)
            sp_away = jcsp[2].get_text(strip=True)

            rq_home = jcrq[0].get_text(strip=True) if jcrq and len(jcrq) >= 3 else None
            rq_draw = jcrq[1].get_text(strip=True) if jcrq and len(jcrq) >= 3 else None
            rq_away = jcrq[2].get_text(strip=True) if jcrq and len(jcrq) >= 3 else None

            matches.append({
                "zgzcw_id": match_id,
                "home_zh": home,
                "away_zh": away,
                "league_zh": league,
                "kickoff_at": date,
                "sp_home": float(sp_home) if sp_home else None,
                "sp_draw": float(sp_draw) if sp_draw else None,
                "sp_away": float(sp_away) if sp_away else None,
                "rq_home": float(rq_home) if rq_home else None,
                "rq_draw": float(rq_draw) if rq_draw else None,
                "rq_away": float(rq_away) if rq_away else None,
            })

        logger.info(f"[zgzcw_jc] fetched {len(matches)} matches with JC SP")
        return matches

    except Exception as e:
        logger.error(f"[zgzcw_jc] fetch failed: {e}")
        return []


def generate_jc_code(home_en: str, away_en: str, kickoff_at: str) -> str:
    """生成竞彩比赛编码：JC-YYYYMMDD-HOME-AWAY"""
    date_part = kickoff_at[:10].replace("-", "")
    home_code = home_en[:3].upper() if len(home_en) >= 3 else home_en.upper()
    away_code = away_en[:3].upper() if len(away_en) >= 3 else away_en.upper()
    return f"JC-{date_part}-{home_code}-{away_code}"


def sync_jc_matches(db_path: str = "database.sqlite") -> Dict:
    """同步 zgzcw 竞彩比赛到数据库"""
    matches = fetch_jc_matches()
    if not matches:
        return {"matches": 0, "created": 0, "updated": 0, "errors": 0}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    created = 0
    updated = 0
    errors = 0

    for m in matches:
        try:
            home_en = _normalise_team_name(m["home_zh"])
            away_en = _normalise_team_name(m["away_zh"])
            league = _normalise_league(m["league_zh"])
            jc_code = generate_jc_code(home_en, away_en, m["kickoff_at"])

            # 查找或创建主队
            cur.execute("SELECT id FROM teams WHERE name_en = ?", (home_en,))
            row = cur.fetchone()
            if row:
                home_id = row[0]
            else:
                # 使用完整队名作为 code，避免重复
                home_code = home_en[:10].upper().replace(" ", "")
                cur.execute(
                    "INSERT OR IGNORE INTO teams (name, name_en, code) VALUES (?, ?, ?)",
                    (m["home_zh"], home_en, home_code),
                )
                cur.execute("SELECT id FROM teams WHERE name_en = ?", (home_en,))
                home_id = cur.fetchone()[0]

            # 查找或创建客队
            cur.execute("SELECT id FROM teams WHERE name_en = ?", (away_en,))
            row = cur.fetchone()
            if row:
                away_id = row[0]
            else:
                away_code = away_en[:10].upper().replace(" ", "")
                cur.execute(
                    "INSERT OR IGNORE INTO teams (name, name_en, code) VALUES (?, ?, ?)",
                    (m["away_zh"], away_en, away_code),
                )
                cur.execute("SELECT id FROM teams WHERE name_en = ?", (away_en,))
                away_id = cur.fetchone()[0]

            # 查找或创建比赛（使用 match_code 作为唯一标识）
            cur.execute("SELECT id FROM matches WHERE match_code = ?", (jc_code,))
            row = cur.fetchone()
            if row:
                match_id = row[0]
                # 更新赔率
                cur.execute(
                    """
                    UPDATE matches
                    SET odds_home = ?, odds_draw = ?, odds_away = ?,
                        odds_source = 'zgzcw', updated_at = ?
                    WHERE id = ?
                    """,
                    (m["sp_home"], m["sp_draw"], m["sp_away"],
                     datetime.now(timezone.utc).isoformat(), match_id),
                )
                updated += 1
            else:
                # 创建新比赛
                cur.execute(
                    """
                    INSERT INTO matches (
                        match_code, home_team_id, away_team_id, kickoff_at,
                        competition, status, odds_home, odds_draw, odds_away,
                        odds_source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        jc_code, home_id, away_id, m["kickoff_at"],
                        league, "SCHEDULED",
                        m["sp_home"], m["sp_draw"], m["sp_away"],
                        "zgzcw", datetime.now(timezone.utc).isoformat(),
                    ),
                )
                match_id = cur.lastrowid
                created += 1

            logger.info(
                f"[zgzcw_jc] {jc_code}: {home_en} vs {away_en} | "
                f"SP={m['sp_home']}/{m['sp_draw']}/{m['sp_away']}"
            )

        except Exception as e:
            errors += 1
            logger.error(f"[zgzcw_jc] sync failed for {m.get('zgzcw_id')}: {e}")

    conn.commit()
    conn.close()

    result = {
        "matches": len(matches),
        "created": created,
        "updated": updated,
        "errors": errors,
    }
    logger.info(f"[zgzcw_jc] sync complete: {result}")
    return result


if __name__ == "__main__":
    result = sync_jc_matches()
    print(f"同步结果: {result}")
