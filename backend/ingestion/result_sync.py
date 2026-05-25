# -*- coding: utf-8 -*-
"""
result_sync.py — 同步已结束比赛结果

Source 1: openfootball .txt 文件（主要，免费，覆盖五大联赛全部比赛）
Source 2: football-data.org API（补充，免费 tier 100 calls/day）
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from models import Match, MatchStatus, Team
from logger import get_logger

logger = get_logger("result_sync")

# ────────────────────────────
# openfootball .txt 文件配置
# ────────────────────────────
_OPENFOOTBALL_TXT = {
    "EPL": ("england", "1-premierleague", "2025-26"),
    "LaLiga": ("espana", "1-liga", "2025-26"),
    "SerieA": ("italy", "1-seriea", "2025-26"),
    "Bundesliga": ("deutschland", "1-bundesliga", "2025-26"),
    "Ligue1": ("france", "2025-26_fr1", "france"),
}

_OPENFOOTBALL_TXT_BASE = "https://raw.githubusercontent.com/openfootball"

# 本地 competition 别名
_COMP_ALIAS = {
    "英超": "EPL", "西甲": "LaLiga", "意甲": "SerieA",
    "德甲": "Bundesliga", "法甲": "Ligue1",
}

# ────────────────────────────
# football-data.org 配置
# ────────────────────────────
FD_COMPETITIONS = {
    "EPL": "PL",
    "LaLiga": "PD",
    "SerieA": "SA",
    "Bundesliga": "BL1",
    "Ligue1": "FL1",
}

# openfootball 长队名 → 本地 name_en 映射
_OF_TO_LOCAL: Dict[str, str] = {
    # EPL
    "Liverpool FC": "Liverpool", "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea", "Manchester United FC": "Manchester United",
    "Manchester City FC": "Man City", "Tottenham Hotspur FC": "Tottenham",
    "Newcastle United FC": "Newcastle", "Aston Villa FC": "Aston Villa",
    "West Ham United FC": "West Ham", "Crystal Palace FC": "Crystal Palace",
    "Everton FC": "Everton", "Brighton & Hove Albion FC": "Brighton",
    "Leeds United FC": "Leeds", "AFC Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford", "Wolverhampton Wanderers FC": "Wolves",
    "Nottingham Forest FC": "Nott'm Forest", "Sunderland AFC": "Sunderland",
    "Burnley FC": "Burnley", "Fulham FC": "Fulham",
    # LaLiga
    "FC Barcelona": "Barcelona", "Real Madrid CF": "Real Madrid",
    "Club Atlético de Madrid": "Atletico Madrid", "Athletic Club": "Athletic Bilbao",
    "Sevilla FC": "Sevilla", "Real Betis Balompié": "Real Betis",
    "Real Sociedad de Fútbol": "Real Sociedad", "Villarreal CF": "Villarreal CF",
    "Valencia CF": "Valencia CF", "RCD Mallorca": "Mallorca",
    "CA Osasuna": "Osasuna", "Getafe CF": "Getafe CF",
    "Levante UD": "Levante UD", "RC Celta de Vigo": "Celta",
    "Girona FC": "Girona", "Elche CF": "Elche CF",
    "Deportivo Alavés": "Deportivo Alavés", "RCD Espanyol de Barcelona": "Espanyol",
    "Rayo Vallecano de Madrid": "Rayo Vallecano", "Real Oviedo": "Real Oviedo",
    "CD Leganés": "CD Leganés", "Real Valladolid": "Real Valladolid",
    "Las Palmas": "Las Palmas",
    # SerieA
    "AC Milan": "AC Milan", "FC Internazionale Milano": "Inter Milan",
    "Juventus FC": "Juventus", "AS Roma": "Roma",
    "SS Lazio": "Lazio", "SSC Napoli": "SSC Napoli",
    "Atalanta BC": "Atalanta", "ACF Fiorentina": "Fiorentina",
    "Bologna FC 1909": "Bologna 1909", "Torino FC": "Torino",
    "Udinese Calcio": "Udinese", "Genoa CFC": "Genoa",
    "Cagliari Calcio": "Cagliari", "US Lecce": "Lecce",
    "Hellas Verona FC": "Verona", "Como 1907": "CMO",
    "Parma Calcio 1913": "Parma Calcio 1913", "US Sassuolo Calcio": "Sassuolo",
    "US Cremonese": "Cremonese", "AC Pisa 1909": "AC Pisa",
    "Monza": "Monza", "Venezia FC": "Venezia",
    # Bundesliga
    "FC Bayern München": "Bayern Munich", "Borussia Dortmund": "Borussia Dortmund",
    "Bayer 04 Leverkusen": "Leverkusen", "RB Leipzig": "RB Leipzig",
    "Eintracht Frankfurt": "Eintracht Frankfurt", "TSG 1899 Hoffenheim": "Hoffenheim",
    "VfL Wolfsburg": "VfL Wolfsburg", "SV Werder Bremen": "Werder Bremen",
    "1. FSV Mainz 05": "Mainz 05", "FC Augsburg": "Augsburg",
    "SC Freiburg": "Freiburg", "Borussia Mönchengladbach": "M'gladbach",
    "FC St. Pauli 1910": "St Pauli", "1. FC Union Berlin": "Union Berlin",
    "1. FC Heidenheim 1846": "1. Heidenheim 1846", "1. FC Köln": "1. Köln",
    "Hamburger SV": "Hamburger SV", "VfB Stuttgart": "Stuttgart",
    # Ligue1
    "Paris Saint-Germain FC": "Paris Saint-Germain",
    "Olympique de Marseille": "Marseille", "AS Monaco FC": "Monaco",
    "Lille OSC": "Lille", "OGC Nice": "OGC Nice",
    "Olympique Lyonnais": "Lyon", "Stade Brestois 29": "Brest",
    "Toulouse FC": "Toulouse", "FC Nantes": "Nantes",
    "Racing Club de Lens": "Racing Club de Lens",
    "Stade Rennais FC 1901": "Stade Rennais 1901",
    "RC Strasbourg Alsace": "RC Strasbourg Alsace",
    "Le Havre AC": "Le Havre", "Angers SCO": "AngersO",
    "FC Metz": "Metz", "FC Lorient": "Lorient",
    "AJ Auxerre": "Auxerre", "Stade de Reims": "Reims",
    "Montpellier HSC": "Montpellier HSC", "Paris FC": "Paris FC",
    "Amiens SC": "Amiens", "AS Saint-Étienne": "AS Saint-Étienne",
    "Girondins de Bordeaux": "Girondins Bordeaux", "Dijon FCO": "DijonO",
    "Nîmes Olympique": "Nîmes Olympique",
}

# football-data.org shortName → 本地 name_en
_FD_TO_LOCAL: Dict[str, str] = {
    "Man City": "Man City", "Man United": "Manchester United",
    "Nott'm Forest": "Nott'm Forest", "Nottingham": "Nott'm Forest",
    "Brighton Hove": "Brighton", "Wolverhampton": "Wolves",
    "Barça": "Barcelona", "Atleti": "Atletico Madrid",
    "Athletic": "Athletic Bilbao", "Sevilla FC": "Sevilla",
    "Alavés": "Deportivo Alavés", "Milan": "AC Milan",
    "Inter": "Inter Milan", "Napoli": "SSC Napoli",
    "Como 1907": "CMO", "Bologna": "Bologna 1909",
    "Parma": "Parma Calcio 1913", "Hellas Verona": "Verona",
    "Dortmund": "Borussia Dortmund", "Bayern": "Bayern Munich",
    "Leverkusen": "Leverkusen", "Frankfurt": "Eintracht Frankfurt",
    "Wolfsburg": "VfL Wolfsburg", "Bremen": "Werder Bremen",
    "Mainz": "Mainz 05", "M'gladbach": "M'gladbach",
    "St. Pauli": "St Pauli", "Heidenheim": "1. Heidenheim 1846",
    "1. FC Köln": "1. Köln", "HSV": "Hamburger SV",
    "PSG": "Paris Saint-Germain", "Olympique Lyon": "Lyon",
    "RC Lens": "Racing Club de Lens", "Stade Rennais": "Stade Rennais 1901",
    "Angers SCO": "AngersO", "FC Metz": "Metz", "Lorient": "Lorient",
    "Auxerre": "Auxerre", "Paris FC": "Paris FC",
}


def _resolve_competition(local_comp: str) -> Optional[str]:
    if local_comp in _OPENFOOTBALL_TXT:
        return local_comp
    return _COMP_ALIAS.get(local_comp)


def _build_name_index(db: Session) -> Dict[str, int]:
    """构建 name_en/name → team_id 索引"""
    teams = db.query(Team).filter(Team.name_en.isnot(None)).all()
    index: Dict[str, int] = {}
    for t in teams:
        if t.name_en:
            index[t.name_en.strip().lower()] = t.id
        if t.name and t.name != t.name_en:
            index[t.name.strip().lower()] = t.id
    return index


def _match_team(source_name: str, name_index: Dict[str, int],
                mapping: Optional[Dict[str, str]] = None) -> Optional[int]:
    """将外部球队名匹配到本地 team_id"""
    # 先查映射表
    if mapping:
        local_name = mapping.get(source_name)
        if local_name:
            key = local_name.strip().lower()
            if key in name_index:
                return name_index[key]

    # 直接匹配
    key = source_name.strip().lower()
    if key in name_index:
        return name_index[key]

    # 安全子串匹配（至少 4 字符）
    if len(key) >= 4:
        for k, tid in name_index.items():
            if f" {key}" in f" {k}" or f" {k}" in f" {key}":
                return tid

    return None


# ────────────────────────────
# Source 1: openfootball .txt
# ────────────────────────────
# 比赛行正则: "Team A FC v Team B FC 3-2 (1-0)" 或 "Team A v Team B 0-0"
_RESULT_RE = re.compile(
    r'^(.+?)\s+v\s+(.+?)\s+(\d+)-(\d+)'
)
# 日期行: "Fri Aug/15 2025" 或 "Sat May/10"
_DATE_RE = re.compile(
    r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})/(\d+)\s*(\d{4})?'
)

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _fetch_openfootball_txt(repo: str, filename: str, season_or_subdir: str) -> Optional[str]:
    """获取 openfootball .txt 文件内容"""
    if season_or_subdir == "france":
        url = f"{_OPENFOOTBALL_TXT_BASE}/{repo}/master/france/{filename}.txt"
    else:
        url = f"{_OPENFOOTBALL_TXT_BASE}/{repo}/master/{season_or_subdir}/{filename}.txt"
    try:
        resp = httpx.get(url, timeout=15.0, headers={"User-Agent": "WC-Analytics/1.0"})
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"[result-sync] openfootball fetch error ({repo}/{filename}): {e}")
        return None


def _parse_results(text: str, season_year: int = 2025) -> List[Dict]:
    """解析 openfootball .txt 格式，提取已结束比赛（含日期）"""
    results = []
    current_round = ""
    current_date = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("»"):
            current_round = line.replace("»", "").strip()
            continue

        # 日期行: "Sat May/10 2026" 或 "Sat May/10"
        dm = _DATE_RE.match(line)
        if dm:
            month_str = dm.group(1)
            day = int(dm.group(2))
            year_str = dm.group(3)
            month = _MONTH_MAP.get(month_str)
            if month:
                year = int(year_str) if year_str else season_year
                try:
                    current_date = datetime(year, month, day)
                except ValueError:
                    current_date = None
            continue

        m = _RESULT_RE.match(line)
        if not m:
            continue

        home_name = m.group(1).strip()
        away_name = m.group(2).strip()
        try:
            hg = int(m.group(3))
            ag = int(m.group(4))
        except ValueError:
            continue

        results.append({
            "home": home_name,
            "away": away_name,
            "home_goals": hg,
            "away_goals": ag,
            "date": current_date,
            "round": current_round,
        })

    return results


def sync_results_from_openfootball(db: Session) -> int:
    """从 openfootball .txt 同步五大联赛比赛结果（主要数据源）"""
    name_index = _build_name_index(db)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)
    pending = (
        db.query(Match)
        .filter(
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.LIVE, MatchStatus.UPCOMING]),
            Match.kickoff_at < cutoff,
        )
        .all()
    )

    if not pending:
        logger.debug("[result-sync] 无待更新的比赛")
        return 0

    by_comp: Dict[str, List[Match]] = {}
    for m in pending:
        comp = _resolve_competition(m.competition or "")
        if comp:
            by_comp.setdefault(comp, []).append(m)

    if not by_comp:
        logger.debug(f"[result-sync] {len(pending)} 场待更新，但无五大联赛比赛")
        return 0

    total_synced = 0

    for comp, local_matches in by_comp.items():
        if comp not in _OPENFOOTBALL_TXT:
            continue

        repo, filename, season = _OPENFOOTBALL_TXT[comp]
        text = _fetch_openfootball_txt(repo, filename, season)
        if not text:
            continue

        of_results = _parse_results(text)
        if not of_results:
            continue

        # 构建 team_id pair → [Match] 索引
        match_index: Dict[Tuple[int, int], List[Match]] = {}
        for m in local_matches:
            key = (m.home_team_id, m.away_team_id)
            match_index.setdefault(key, []).append(m)

        synced_in_comp = 0
        for r in of_results:
            home_id = _match_team(r["home"], name_index, _OF_TO_LOCAL)
            away_id = _match_team(r["away"], name_index, _OF_TO_LOCAL)

            if not home_id or not away_id:
                continue

            # 查找本地比赛
            candidates = match_index.get((home_id, away_id), [])
            reversed_cands = match_index.get((away_id, home_id), [])
            swapped = False

            if not candidates and reversed_cands:
                candidates = reversed_cands
                swapped = True

            if not candidates:
                continue

            # 选日期最接近的未 FINISHED 比赛
            best = None
            best_dist = 9999
            of_date = r.get("date")
            for m in candidates:
                if m.status == MatchStatus.FINISHED:
                    continue
                if of_date and m.kickoff_at:
                    m_date = m.kickoff_at.replace(tzinfo=None)
                    dist = abs((of_date - m_date).days)
                else:
                    dist = 0
                if dist < best_dist:
                    best_dist = dist
                    best = m

            if not best:
                continue

            # 只匹配日期差距 ≤3 天的比赛（防止错误匹配不同轮次）
            if best_dist > 3:
                continue

            hg = r["away_goals"] if swapped else r["home_goals"]
            ag = r["home_goals"] if swapped else r["away_goals"]
            best.actual_home_goals = hg
            best.actual_away_goals = ag
            best.actual_outcome = "home" if hg > ag else ("draw" if hg == ag else "away")
            best.status = MatchStatus.FINISHED
            db.commit()
            synced_in_comp += 1

            h_name = db.query(Team).filter(Team.id == best.home_team_id).first()
            a_name = db.query(Team).filter(Team.id == best.away_team_id).first()
            logger.info(
                f"[result-sync] {comp}: {h_name.name if h_name else '?'} {hg}-{ag} "
                f"{a_name.name if a_name else '?'}"
            )

        if synced_in_comp:
            total_synced += synced_in_comp
            logger.info(f"[result-sync] {comp}: 同步 {synced_in_comp} 场结果")

    if total_synced:
        logger.info(f"[result-sync] openfootball 共更新 {total_synced} 场比赛结果")
    else:
        logger.debug(f"[result-sync] 检查了 {len(pending)} 场比赛，无新结果")

    return total_synced


# ────────────────────────────
# Source 2: football-data.org API（补充）
# ────────────────────────────
def _fetch_fd_matches(fd_comp: str, date_from: str, date_to: str, api_key: str) -> List[Dict]:
    url = f"https://api.football-data.org/v4/competitions/{fd_comp}/matches"
    params = {"status": "FINISHED", "dateFrom": date_from, "dateTo": date_to}
    headers = {"X-Auth-Token": api_key}

    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        return resp.json().get("matches", [])
    except Exception as e:
        logger.warning(f"[result-sync] football-data API 请求失败 ({fd_comp}): {e}")
        return []


def sync_results_from_football_data(db: Session, days_back: int = 7) -> int:
    """从 football-data.org 同步五大联赛比赛结果（补充数据源）"""
    from config import get_settings
    api_key = os.getenv("FOOTBALL_DATA_API_KEY") or get_settings().FOOTBALL_DATA_API_KEY
    if not api_key:
        return 0

    name_index = _build_name_index(db)
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)
    pending = (
        db.query(Match)
        .filter(
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.LIVE, MatchStatus.UPCOMING]),
            Match.kickoff_at < cutoff,
        )
        .all()
    )

    if not pending:
        return 0

    by_comp: Dict[str, List[Match]] = {}
    for m in pending:
        comp = _resolve_competition(m.competition or "")
        if comp:
            by_comp.setdefault(comp, []).append(m)

    if not by_comp:
        return 0

    total_synced = 0

    for comp, local_matches in by_comp.items():
        fd_comp = FD_COMPETITIONS.get(comp)
        if not fd_comp:
            continue

        fd_matches = _fetch_fd_matches(fd_comp, date_from, date_to, api_key)
        if not fd_matches:
            continue

        match_index: Dict[Tuple[int, int], List[Match]] = {}
        for m in local_matches:
            match_index.setdefault((m.home_team_id, m.away_team_id), []).append(m)

        for fm in fd_matches:
            score = fm.get("score", {}).get("fullTime", {})
            hg_val = score.get("home")
            ag_val = score.get("away")
            if hg_val is None or ag_val is None:
                continue

            home_id = _match_team(fm["homeTeam"].get("shortName", ""), name_index, _FD_TO_LOCAL)
            away_id = _match_team(fm["awayTeam"].get("shortName", ""), name_index, _FD_TO_LOCAL)
            if not home_id or not away_id:
                continue

            candidates = match_index.get((home_id, away_id), [])
            reversed_cands = match_index.get((away_id, home_id), [])
            swapped = False
            if not candidates and reversed_cands:
                candidates = reversed_cands
                swapped = True
            if not candidates:
                continue

            best = None
            for m in candidates:
                if m.status != MatchStatus.FINISHED:
                    best = m
                    break
            if not best:
                continue

            hg = int(ag_val) if swapped else int(hg_val)
            ag = int(hg_val) if swapped else int(ag_val)
            best.actual_home_goals = hg
            best.actual_away_goals = ag
            best.actual_outcome = "home" if hg > ag else ("draw" if hg == ag else "away")
            best.status = MatchStatus.FINISHED
            db.commit()
            total_synced += 1

    return total_synced


# ────────────────────────────
# 统一入口
# ────────────────────────────
def sync_results(db: Session, days_back: int = 14) -> int:
    """
    同步所有数据源的已结束比赛结果。

    1. 先尝试 openfootball .txt（覆盖最全）
    2. 再尝试 football-data.org API（补充）
    """
    total = 0

    count1 = sync_results_from_openfootball(db)
    total += count1

    count2 = sync_results_from_football_data(db, days_back=days_back)
    total += count2

    if total:
        logger.info(f"[result-sync] 总计更新 {total} 场比赛结果")
    return total
