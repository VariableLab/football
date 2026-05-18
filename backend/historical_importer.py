#!/usr/bin/env python3
"""
历史比赛数据导入器

从 football-data.co.uk 下载各联赛历史比赛 CSV，导入到数据库。
用于：
1. 扩充训练数据集 → 提升模型准确性
2. 计算球队 xG / 近期状态
3. 闭市赔率校准

赔率优先级（开盘/收盘分别处理）：
- 开盘: B365 > PS(Pinnacle) > BW(Bwin) > WH(William Hill) > IW(Interwetten)
- 收盘: PSC(Pinnacle Closing) > B365C(B365 Closing) > BWC > WHC

支持联赛：
- 国际比赛（世界杯、欧洲杯、世预赛）
- 英超、西甲、意甲、德甲、法甲

用法：
python historical_importer.py                # 导入全部
python historical_importer.py --league intl  # 仅国际比赛
python historical_importer.py --season 2425  # 仅 2024-25 赛季
python historical_importer.py --dry-run      # 仅预览不写入
python historical_importer.py --backfill     # 回填已有比赛的收盘赔率
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from models import SessionLocal, Team, Match, MatchStatus, MatchType
from logger import get_logger

logger = get_logger("historical_importer")

# ────────────────────────────
# football-data.co.uk 联赛 URL
# ────────────────────────────
# 格式: https://www.football-data.co.uk/mmz4281/{season}/{code}.csv
# season: 2425 = 2024-25, 2324 = 2023-24, etc.
# code: E0=EPL, SP1=LaLiga, I1=SerieA, D1=Bundesliga, F1=Ligue1

LEAGUE_CODES = {
    "epl": "E0",
    "laliga": "SP1",
    "seriea": "I1",
    "bundesliga": "D1",
    "ligue1": "F1",
}

SEASONS = ["2425", "2324", "2223", "2122", "2021", "1920", "1819", "1718", "1617", "1516", "1415", "1314", "1213", "1112", "1011", "0910", "0809", "0708", "0607", "0506"]

INTERNATIONAL_URL = "https://www.football-data.co.uk/new/newinternational.csv"

# ────────────────────────────
# 赔率列优先级
# ────────────────────────────
# 开盘赔率: 优先 Bet365, 其次 Pinnacle, Bwin, William Hill
OPENING_ODDS_COLS = [
    ("B365H", "B365D", "B365A"),     # Bet365 开盘
    ("PSH", "PSD", "PSA"),            # Pinnacle 开盘
    ("BWH", "BWD", "BWA"),            # Bwin 开盘
    ("WHH", "WHD", "WHA"),            # William Hill 开盘
]

# 收盘赔率: 优先 Pinnacle Closing, 其次 B365 Closing
CLOSING_ODDS_COLS = [
    ("PSCH", "PSCD", "PSCA"),         # Pinnacle 收盘（最接近真实收盘）
    ("B365CH", "B365CD", "B365CA"),   # Bet365 收盘
    ("BWCH", "BWCD", "BWCA"),         # Bwin 收盘
    ("WHCH", "WHCD", "WHCA"),         # William Hill 收盘
]

# 英文队名 → 内部 code 映射（部分常用队）
# 从数据库动态补充
_TEAM_NAME_MAP: Dict[str, str] = {}


def _build_team_map(db: Session) -> Dict[str, int]:
    """从数据库构建 队名 → team_id 映射"""
    teams = db.query(Team).all()
    name_map = {}
    for t in teams:
        if t.name_en:
            name_map[t.name_en.lower().strip()] = t.id
        if t.name:
            name_map[t.name.lower().strip()] = t.id
        if t.code:
            name_map[t.code.lower()] = t.id
    return name_map


def _find_or_create_team(
    db: Session,
    team_name: str,
    name_map: Dict[str, int],
    league: str = "",
) -> Optional[int]:
    """查找或创建球队，返回 team_id"""
    key = team_name.lower().strip()
    if key in name_map:
        return name_map[key]

    # 优先使用规范名匹配 (data_cleaner)
    from data_cleaner import resolve_team_db, resolve_team_name
    canonical = resolve_team_name(team_name)
    db_id = resolve_team_db(db, team_name)
    if db_id:
        name_map[key] = db_id
        return db_id

    # 模糊匹配 (降低后的优先级)
    for existing_name, team_id in name_map.items():
        if len(key) >= 5 and (key in existing_name or existing_name in key):
            return team_id

    # 创建新球队
    code = team_name[:3].upper()
    # 确保唯一
    existing = db.query(Team).filter(Team.code == code).first()
    if existing:
        if league:
            code = f"{code}_{league[:3].upper()}"
        else:
            import hashlib
            code = f"{code}_{hashlib.md5(team_name.encode()).hexdigest()[:4].upper()}"

    team = Team(
        name=team_name,
        name_en=team_name,
        code=code,
        elo=1500,
        avg_goals_scored=1.20,
        avg_goals_conceded=1.10,
    )
    db.add(team)
    db.flush()
    name_map[key] = team.id
    logger.info(f"[import] Created team: {team_name} ({code}) id={team.id}")
    return team.id


def _parse_date(date_str: str) -> Optional[datetime]:
    """解析 CSV 日期格式（DD/MM/YY 或 DD/MM/YYYY）"""
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
    return None


def _safe_float(val) -> Optional[float]:
    try:
        v = float(val)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _extract_odds_by_priority(row: Dict, col_groups: List[Tuple[str, str, str]]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
    """按优先级提取赔率，返回 (home, draw, away, source)"""
    for h_col, d_col, a_col in col_groups:
        h = _safe_float(row.get(h_col))
        d = _safe_float(row.get(d_col))
        a = _safe_float(row.get(a_col))
        if h and d and a:
            source = h_col.replace("H", "").replace("CH", "C")
            return h, d, a, source
    return None, None, None, None


def download_csv(url: str) -> List[Dict]:
    """下载 CSV 并解析为 dict 列表"""
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text.lstrip("﻿")  # 去BOM
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        logger.info(f"[download] {url} → {len(rows)} rows")
        return rows
    except Exception as e:
        logger.error(f"[download] Failed {url}: {e}")
        return []
    finally:
        client.close()


def _make_match_code(prefix: str, kickoff: datetime, home: str, away: str) -> str:
    date_code = kickoff.strftime("%Y%m%d")
    return f"{prefix}-{date_code}-{home[:3].upper()}{away[:3].upper()}"


def import_international_matches(
    db: Session,
    name_map: Dict[str, int],
    dry_run: bool = False,
) -> Dict:
    """导入国际比赛数据（含世界杯、欧洲杯、世预赛）"""
    rows = download_csv(INTERNATIONAL_URL)
    if not rows:
        return {"total": 0, "imported": 0, "skipped": 0, "created": 0}

    stats = {"total": len(rows), "imported": 0, "skipped": 0, "created": 0}

    for row in rows:
        home_name = row.get("HomeTeam", "").strip()
        away_name = row.get("AwayTeam", "").strip()
        date_str = row.get("Date", "").strip()

        if not home_name or not away_name or not date_str:
            continue

        kickoff = _parse_date(date_str)
        if not kickoff:
            continue

        home_goals = _safe_int(row.get("FTHG"))
        away_goals = _safe_int(row.get("FTAG"))

        # 开盘赔率
        open_h, open_d, open_a, open_src = _extract_odds_by_priority(row, OPENING_ODDS_COLS)
        # 收盘赔率
        close_h, close_d, close_a, close_src = _extract_odds_by_priority(row, CLOSING_ODDS_COLS)
        # 通用赔率（兼容旧行: 优先开盘, fallback收盘）
        odds_h = open_h or close_h
        odds_d = open_d or close_d
        odds_a = open_a or close_a

        # 确定比赛类型
        tournament = row.get("Tournament", "")
        match_type = MatchType.FRIENDLY
        if "world cup" in (tournament or "").lower():
            match_type = MatchType.WORLD_CUP
        elif "qualif" in (tournament or "").lower():
            match_type = MatchType.QUALIFIER

        # 跳过未来比赛
        if kickoff > datetime.now(timezone.utc) + timedelta(days=1):
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["imported"] += 1
            continue

        home_id = _find_or_create_team(db, home_name, name_map, "intl")
        away_id = _find_or_create_team(db, away_name, name_map, "intl")

        if not home_id or not away_id:
            stats["skipped"] += 1
            continue

        match_code = _make_match_code("INT", kickoff, home_name, away_name)

        existing = db.query(Match).filter(Match.match_code == match_code).first()
        if existing:
            # 更新已有比赛的收盘赔率
            if close_h and not existing.closing_odds_home:
                existing.closing_odds_home = close_h
                existing.closing_odds_draw = close_d
                existing.closing_odds_away = close_a
                existing.closing_odds_source = f"football-data-{close_src}"
            stats["skipped"] += 1
            continue

        actual_outcome = None
        if home_goals is not None and away_goals is not None:
            if home_goals > away_goals:
                actual_outcome = "home"
            elif home_goals == away_goals:
                actual_outcome = "draw"
            else:
                actual_outcome = "away"

        status = MatchStatus.FINISHED if actual_outcome else MatchStatus.SCHEDULED

        match = Match(
            match_code=match_code,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=kickoff,
            status=status,
            match_type=match_type,
            competition=tournament or "International",
            odds_home=odds_h,
            odds_draw=odds_d,
            odds_away=odds_a,
            odds_source=f"football-data-{open_src}" if open_src else None,
            opening_odds_home=open_h,
            opening_odds_draw=open_d,
            opening_odds_away=open_a,
            opening_odds_source=f"football-data-{open_src}" if open_src else None,
            closing_odds_home=close_h,
            closing_odds_draw=close_d,
            closing_odds_away=close_a,
            closing_odds_source=f"football-data-{close_src}" if close_src else None,
            actual_home_goals=home_goals,
            actual_away_goals=away_goals,
            actual_outcome=actual_outcome,
        )
        db.add(match)
        stats["imported"] += 1

    if not dry_run:
        db.commit()

    return stats


def import_league_matches(
    db: Session,
    name_map: Dict[str, int],
    league: str,
    season: str,
    dry_run: bool = False,
) -> Dict:
    """导入联赛历史数据"""
    code = LEAGUE_CODES.get(league)
    if not code:
        logger.error(f"Unknown league: {league}")
        return {"total": 0, "imported": 0, "skipped": 0}

    url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
    rows = download_csv(url)
    if not rows:
        return {"total": 0, "imported": 0, "skipped": 0}

    stats = {"total": len(rows), "imported": 0, "skipped": 0, "closing_updated": 0}

    for row in rows:
        home_name = row.get("HomeTeam", "").strip()
        away_name = row.get("AwayTeam", "").strip()
        date_str = row.get("Date", "").strip()

        if not home_name or not away_name or not date_str:
            continue

        kickoff = _parse_date(date_str)
        if not kickoff:
            continue

        home_goals = _safe_int(row.get("FTHG"))
        away_goals = _safe_int(row.get("FTAG"))

        # 开盘赔率
        open_h, open_d, open_a, open_src = _extract_odds_by_priority(row, OPENING_ODDS_COLS)
        # 收盘赔率
        close_h, close_d, close_a, close_src = _extract_odds_by_priority(row, CLOSING_ODDS_COLS)
        # 通用赔率
        odds_h = open_h or close_h
        odds_d = open_d or close_d
        odds_a = open_a or close_a

        if dry_run:
            stats["imported"] += 1
            continue

        home_id = _find_or_create_team(db, home_name, name_map, league)
        away_id = _find_or_create_team(db, away_name, name_map, league)

        if not home_id or not away_id:
            stats["skipped"] += 1
            continue

        season_label = f"20{season[:2]}-{season[2:]}"
        match_code = _make_match_code(f"{league.upper()}-{season}", kickoff, home_name, away_name)

        existing = db.query(Match).filter(Match.match_code == match_code).first()
        if existing:
            # 更新已有比赛的收盘赔率
            if close_h and not existing.closing_odds_home:
                existing.closing_odds_home = close_h
                existing.closing_odds_draw = close_d
                existing.closing_odds_away = close_a
                existing.closing_odds_source = f"football-data-{close_src}"
                stats["closing_updated"] += 1
            # 更新开盘赔率
            if open_h and not existing.opening_odds_home:
                existing.opening_odds_home = open_h
                existing.opening_odds_draw = open_d
                existing.opening_odds_away = open_a
                existing.opening_odds_source = f"football-data-{open_src}"
            stats["skipped"] += 1
            continue

        actual_outcome = None
        if home_goals is not None and away_goals is not None:
            if home_goals > away_goals:
                actual_outcome = "home"
            elif home_goals == away_goals:
                actual_outcome = "draw"
            else:
                actual_outcome = "away"

        status = MatchStatus.FINISHED if actual_outcome else MatchStatus.SCHEDULED

        match = Match(
            match_code=match_code,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=kickoff,
            status=status,
            match_type=MatchType.FRIENDLY,
            competition=f"{league.upper()} {season_label}",
            odds_home=odds_h,
            odds_draw=odds_d,
            odds_away=odds_a,
            odds_source=f"football-data-{open_src}" if open_src else None,
            opening_odds_home=open_h,
            opening_odds_draw=open_d,
            opening_odds_away=open_a,
            opening_odds_source=f"football-data-{open_src}" if open_src else None,
            closing_odds_home=close_h,
            closing_odds_draw=close_d,
            closing_odds_away=close_a,
            closing_odds_source=f"football-data-{close_src}" if close_src else None,
            actual_home_goals=home_goals,
            actual_away_goals=away_goals,
            actual_outcome=actual_outcome,
        )
        db.add(match)
        stats["imported"] += 1

    if not dry_run:
        db.commit()

    return stats


# ────────────────────────────
# 回填已有比赛的收盘赔率
# ────────────────────────────
def backfill_closing_odds(db: Session, dry_run: bool = False) -> Dict:
    """
    回填已有25K+比赛的 closing_odds 和 opening_odds。
    用日期+队名模糊匹配 football-data.co.uk CSV 行到数据库比赛。
    """
    name_map = _build_team_map(db)

    stats = {"total_csv_rows": 0, "matched": 0, "closing_updated": 0, "opening_updated": 0, "no_match": 0}

    for league, code in LEAGUE_CODES.items():
        for season in SEASONS:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            rows = download_csv(url)
            if not rows:
                continue

            stats["total_csv_rows"] += len(rows)
            updated_this_batch = 0

            for row in rows:
                home_name = row.get("HomeTeam", "").strip()
                away_name = row.get("AwayTeam", "").strip()
                date_str = row.get("Date", "").strip()
                if not home_name or not away_name or not date_str:
                    continue

                kickoff = _parse_date(date_str)
                if not kickoff:
                    continue

                # 用队名映射找team_id
                home_id = name_map.get(home_name.lower().strip())
                away_id = name_map.get(away_name.lower().strip())

                if not home_id or not away_id:
                    # 尝试模糊匹配
                    for existing_name, tid in name_map.items():
                        if not home_id and home_name.lower().strip() in existing_name:
                            home_id = tid
                        if not away_id and away_name.lower().strip() in existing_name:
                            away_id = tid
                        if home_id and away_id:
                            break

                if not home_id or not away_id:
                    stats["no_match"] += 1
                    continue

                # 用日期+team_id匹配比赛（允许±12小时时差）
                day_start = kickoff.replace(hour=0, minute=0, second=0)
                day_end = day_start + timedelta(days=1)
                match = db.query(Match).filter(
                    Match.home_team_id == home_id,
                    Match.away_team_id == away_id,
                    Match.kickoff_at >= day_start,
                    Match.kickoff_at < day_end,
                ).first()

                if not match:
                    stats["no_match"] += 1
                    continue

                stats["matched"] += 1

                # 提取收盘赔率
                close_h, close_d, close_a, close_src = _extract_odds_by_priority(row, CLOSING_ODDS_COLS)
                if close_h and not match.closing_odds_home:
                    match.closing_odds_home = close_h
                    match.closing_odds_draw = close_d
                    match.closing_odds_away = close_a
                    match.closing_odds_source = f"football-data-{close_src}"
                    stats["closing_updated"] += 1
                    updated_this_batch += 1

                # 提取开盘赔率
                open_h, open_d, open_a, open_src = _extract_odds_by_priority(row, OPENING_ODDS_COLS)
                if open_h and not match.opening_odds_home:
                    match.opening_odds_home = open_h
                    match.opening_odds_draw = open_d
                    match.opening_odds_away = open_a
                    match.opening_odds_source = f"football-data-{open_src}"
                    stats["opening_updated"] += 1

                # 也更新odds_*字段（如果为空）
                if open_h and not match.odds_home:
                    match.odds_home = open_h
                    match.odds_draw = open_d
                    match.odds_away = open_a
                    match.odds_source = f"football-data-{open_src}"

            if not dry_run and updated_this_batch > 0:
                db.commit()
            logger.info(f"[backfill] {league} {season}: +{updated_this_batch} closing odds")

    return stats


def import_all(
    db: Session,
    dry_run: bool = False,
    league: str = None,
    season: str = None,
) -> Dict:
    """导入所有历史数据"""
    name_map = _build_team_map(db)
    total_stats = {"imported": 0, "skipped": 0, "total": 0}

    # 国际比赛
    if not league or league == "intl":
        logger.info("[import] Starting international matches import...")
        stats = import_international_matches(db, name_map, dry_run)
        logger.info(f"[import] International: {stats}")
        total_stats["imported"] += stats["imported"]
        total_stats["skipped"] += stats["skipped"]
        total_stats["total"] += stats["total"]

    # 联赛数据
    target_leagues = [league] if league and league != "intl" else list(LEAGUE_CODES.keys())
    target_seasons = [season] if season else SEASONS

    for lg in target_leagues:
        for ss in target_seasons:
            logger.info(f"[import] Starting {lg} {ss} import...")
            stats = import_league_matches(db, name_map, lg, ss, dry_run)
            logger.info(f"[import] {lg} {ss}: {stats}")
            total_stats["imported"] += stats["imported"]
            total_stats["skipped"] += stats["skipped"]
            total_stats["total"] += stats["total"]

    return total_stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Historical match data importer")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--league", type=str, help="League code (intl/epl/laliga/seriea/bundesliga/ligue1)")
    parser.add_argument("--season", type=str, help="Season code (e.g. 2425, 2324)")
    parser.add_argument("--backfill", action="store_true", help="Backfill closing odds for existing matches")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.backfill:
            logger.info("[backfill] Starting closing odds backfill...")
            stats = backfill_closing_odds(db, dry_run=args.dry_run)
            logger.info(f"[backfill] Complete: {stats}")
        else:
            stats = import_all(db, dry_run=args.dry_run, league=args.league, season=args.season)
            logger.info(f"[import] Complete: {stats}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
