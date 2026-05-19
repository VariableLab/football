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
import json
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.zgzcw.com/",
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


def _get_issue_id_for_date(date_str: str) -> str:
    """从比赛日期生成期号：2026-05-19 -> JC20260519"""
    return f"JC{date_str[:10].replace('-', '')}"


def _sync_issue_links(cur, match_id: int, jc_code: str, kickoff_at: str, sp_home: float, sp_draw: float, sp_away: float, rq_home, rq_draw, rq_away) -> bool:
    """创建 jingcai_issue + jingcai_issue_matches 关联"""
    try:
        match_date = kickoff_at[:10] if kickoff_at else ""
        if not match_date:
            return False

        issue_id = _get_issue_id_for_date(match_date)

        # 查找或创建期号
        cur.execute("SELECT id FROM jingcai_issues WHERE issue_id = ?", (issue_id,))
        row = cur.fetchone()
        if row:
            ji_id = row[0]
        else:
            cur.execute(
                "INSERT INTO jingcai_issues (issue_id, issue_type, status, created_at) VALUES (?, ?, ?, ?)",
                (issue_id, "spf14", "on_sale", datetime.now(timezone.utc).isoformat()),
            )
            ji_id = cur.lastrowid

        # 检查关联是否已存在
        cur.execute(
            "SELECT id FROM jingcai_issue_matches WHERE issue_id = ? AND match_id = ?",
            (ji_id, match_id),
        )
        if cur.fetchone():
            return True

        # 获取下一个 sequence
        cur.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM jingcai_issue_matches WHERE issue_id = ?",
            (ji_id,),
        )
        seq = cur.fetchone()[0]

        # 构建让球赔率 JSON
        rq_odds = None
        if rq_home is not None:
            rq_odds = json.dumps({
                "h": str(rq_home), "d": str(rq_draw), "a": str(rq_away),
            }, ensure_ascii=False)

        cur.execute(
            """
            INSERT INTO jingcai_issue_matches
                (issue_id, match_id, sequence, handicap, rq_odds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ji_id, match_id, seq, 0, rq_odds),
        )

        logger.info(f"[zgzcw_jc] Linked {jc_code} -> {issue_id} (seq={seq})")
        return True
    except Exception as e:
        logger.error(f"[zgzcw_jc] issue link failed for {jc_code}: {e}")
        return False


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
    issues_linked = 0
    now_utc = datetime.now(timezone.utc)

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
            cur.execute("SELECT id, opening_odds_home, odds_home FROM matches WHERE match_code = ?", (jc_code,))
            row = cur.fetchone()
            if row:
                match_id = row[0]
                had_opening = row[1] is not None
                prev_odds_home = row[2]
                # 更新赔率
                cur.execute(
                    """
                    UPDATE matches
                    SET odds_home = ?, odds_draw = ?, odds_away = ?,
                        odds_source = 'zgzcw', updated_at = ?
                    WHERE id = ?
                    """,
                    (m["sp_home"], m["sp_draw"], m["sp_away"],
                     now_utc.isoformat(), match_id),
                )
                # 首次出现则记录开盘价
                if not had_opening:
                    cur.execute(
                        """
                        UPDATE matches
                        SET opening_odds_home = ?, opening_odds_draw = ?, opening_odds_away = ?,
                            opening_odds_source = 'zgzcw', opening_odds_at = ?
                        WHERE id = ?
                        """,
                        (m["sp_home"], m["sp_draw"], m["sp_away"], now_utc.isoformat(), match_id),
                    )
                # 赔率有变化则记录历史
                if prev_odds_home != m["sp_home"] and m["sp_home"] is not None:
                    cur.execute(
                        """
                        INSERT INTO odds_history (match_id, source, odds_home, odds_draw, odds_away, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (match_id, "zgzcw", m["sp_home"], m["sp_draw"], m["sp_away"], now_utc.isoformat()),
                    )
                updated += 1
            else:
                # 创建新比赛
                cur.execute(
                    """
                    INSERT INTO matches (
                        match_code, home_team_id, away_team_id, kickoff_at,
                        competition, status, odds_home, odds_draw, odds_away,
                        odds_source, opening_odds_home, opening_odds_draw, opening_odds_away,
                        opening_odds_source, opening_odds_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        jc_code, home_id, away_id, m["kickoff_at"],
                        league, "SCHEDULED",
                        m["sp_home"], m["sp_draw"], m["sp_away"],
                        "zgzcw",
                        m["sp_home"], m["sp_draw"], m["sp_away"],
                        "zgzcw", now_utc.isoformat(),
                        now_utc.isoformat(),
                    ),
                )
                match_id = cur.lastrowid
                created += 1

            # 创建期号+比赛关联
            if _sync_issue_links(cur, match_id, jc_code, m["kickoff_at"],
                                  m["sp_home"], m["sp_draw"], m["sp_away"],
                                  m.get("rq_home"), m.get("rq_draw"), m.get("rq_away")):
                issues_linked += 1

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
        "issues_linked": issues_linked,
    }
    logger.info(f"[zgzcw_jc] sync complete: {result}")
    return result


def full_sync_jc_matches(db_path: str = "database.sqlite") -> Dict:
    """全量同步：比赛数据 + 期号关联 + 赔率历史"""
    return sync_jc_matches(db_path)


if __name__ == "__main__":
    result = sync_jc_matches()
    print(f"同步结果: {result}")
