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
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from utils.logger import get_logger

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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.zgzcw.com/",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
    }

    try:
        time.sleep(random.uniform(0.3, 1.0))
        resp = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        # 418 反爬时重试一次（换 UA）
        if resp.status_code == 418:
            headers["User-Agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
            time.sleep(random.uniform(1.0, 2.0))
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


def _sync_issue_links(db, match_id: int, jc_code: str, kickoff_at: str, sp_home: float, sp_draw: float, sp_away: float, rq_home, rq_draw, rq_away) -> bool:
    """创建 jingcai_issue + jingcai_issue_matches 关联"""
    from database.models import JingcaiIssue, JingcaiIssueMatch
    try:
        match_date = kickoff_at[:10] if kickoff_at else ""
        if not match_date:
            return False

        issue_id = _get_issue_id_for_date(match_date)

        issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
        if not issue:
            issue = JingcaiIssue(issue_id=issue_id, issue_type="spf14", status="on_sale")
            db.add(issue)
            db.flush()

        link = db.query(JingcaiIssueMatch).filter(
            JingcaiIssueMatch.issue_id == issue.id,
            JingcaiIssueMatch.match_id == match_id
        ).first()
        
        if link:
            return True

        max_seq = db.query(JingcaiIssueMatch).filter(JingcaiIssueMatch.issue_id == issue.id).count()
        
        rq_odds = None
        if rq_home is not None:
            rq_odds = json.dumps({"h": str(rq_home), "d": str(rq_draw), "a": str(rq_away)}, ensure_ascii=False)

        link = JingcaiIssueMatch(
            issue_id=issue.id,
            match_id=match_id,
            sequence=max_seq + 1,
            handicap=0,
            rq_odds=rq_odds
        )
        db.add(link)
        db.flush()

        logger.info(f"[zgzcw_jc] Linked {jc_code} -> {issue_id} (seq={max_seq + 1})")
        return True
    except Exception as e:
        logger.error(f"[zgzcw_jc] issue link failed for {jc_code}: {e}")
        return False


def sync_jc_matches(db_path: str = None) -> Dict:
    """同步 zgzcw 竞彩比赛到数据库"""
    from database.models import SessionLocal, Team, Match, OddsHistory
    
    matches = fetch_jc_matches()
    if not matches:
        return {"matches": 0, "created": 0, "updated": 0, "errors": 0, "issues_linked": 0}

    # 💡 支持自定义数据库路径（用于本地同步脚本）
    if db_path:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        # 确保是绝对路径
        if not db_path.startswith("sqlite:///"):
            db_url = f"sqlite:///{os.path.abspath(db_path)}"
        else:
            db_url = db_path
        engine = create_engine(db_url)
        db_session_factory = sessionmaker(bind=engine)
        db = db_session_factory()
    else:
        db = SessionLocal()

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
            home_team = db.query(Team).filter(Team.name_en == home_en).first()
            if not home_team:
                home_team = Team(name=m["home_zh"], name_en=home_en, code=home_en[:10].upper().replace(" ", ""))
                db.add(home_team)
                db.flush()
                
            # 查找或创建客队
            away_team = db.query(Team).filter(Team.name_en == away_en).first()
            if not away_team:
                away_team = Team(name=m["away_zh"], name_en=away_en, code=away_en[:10].upper().replace(" ", ""))
                db.add(away_team)
                db.flush()

            # 查找比赛
            match = db.query(Match).filter(Match.match_code == jc_code).first()
            if match:
                had_opening = match.opening_odds_home is not None
                prev_odds_home = match.odds_home
                
                match.odds_home = m["sp_home"]
                match.odds_draw = m["sp_draw"]
                match.odds_away = m["sp_away"]
                match.odds_source = "zgzcw"
                match.updated_at = now_utc
                
                if not had_opening:
                    match.opening_odds_home = m["sp_home"]
                    match.opening_odds_draw = m["sp_draw"]
                    match.opening_odds_away = m["sp_away"]
                    match.opening_odds_source = "zgzcw"
                    match.opening_odds_at = now_utc
                    
                if prev_odds_home != m["sp_home"] and m["sp_home"] is not None:
                    history = OddsHistory(
                        match_id=match.id,
                        source="zgzcw",
                        odds_home=m["sp_home"],
                        odds_draw=m["sp_draw"],
                        odds_away=m["sp_away"],
                        recorded_at=now_utc
                    )
                    db.add(history)
                updated += 1
            else:
                try:
                    if isinstance(m["kickoff_at"], str):
                        kickoff_dt = datetime.fromisoformat(m["kickoff_at"]) if "T" in m["kickoff_at"] else datetime.strptime(m["kickoff_at"], "%Y-%m-%d %H:%M:%S")
                    else:
                        kickoff_dt = m["kickoff_at"]
                except Exception:
                    kickoff_dt = None
                    
                match = Match(
                    match_code=jc_code,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    kickoff_at=kickoff_dt,
                    competition=league,
                    status="SCHEDULED",
                    odds_home=m["sp_home"],
                    odds_draw=m["sp_draw"],
                    odds_away=m["sp_away"],
                    odds_source="zgzcw",
                    opening_odds_home=m["sp_home"],
                    opening_odds_draw=m["sp_draw"],
                    opening_odds_away=m["sp_away"],
                    opening_odds_source="zgzcw",
                    opening_odds_at=now_utc
                )
                db.add(match)
                db.flush()
                created += 1

            if _sync_issue_links(db, match.id, jc_code, m["kickoff_at"],
                                  m["sp_home"], m["sp_draw"], m["sp_away"],
                                  m.get("rq_home"), m.get("rq_draw"), m.get("rq_away")):
                issues_linked += 1

            db.commit()
            
            # 触发 AI 预测引擎自动重算
            from core.prediction_recalc import on_odds_updated
            on_odds_updated(db, match.id)

            logger.info(
                f"[zgzcw_jc] {jc_code}: {home_en} vs {away_en} | "
                f"SP={m['sp_home']}/{m['sp_draw']}/{m['sp_away']}"
            )

        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"[zgzcw_jc] sync failed for {m.get('zgzcw_id')}: {e}")

    db.close()

    result = {
        "matches": len(matches),
        "created": created,
        "updated": updated,
        "errors": errors,
        "issues_linked": issues_linked,
    }
    logger.info(f"[zgzcw_jc] sync complete: {result}")
    return result


def full_sync_jc_matches(db_path: str = None) -> Dict:
    """全量同步：比赛数据 + 期号关联 + 赔率历史"""
    return sync_jc_matches(db_path)


if __name__ == "__main__":
    result = sync_jc_matches()
    print(f"同步结果: {result}")
