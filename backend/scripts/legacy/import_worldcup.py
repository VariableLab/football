#!/usr/bin/env python3
"""
世界杯历史数据导入器

从 openfootball/world-cup.json 拉取所有世界杯比赛数据（1930-2022）。
正确处理淘汰赛（SPF 仅基于 90 分钟常规赛结果，不含加时/点球）。

用法：
  python import_worldcup.py                     # 导入所有
  python import_worldcup.py --years 2018,2022   # 仅指定年份
  python import_worldcup.py --dry-run           # 仅预览
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

import httpx
from sqlalchemy.orm import Session

from database.models import SessionLocal, Team, Match, MatchStatus, MatchType
from utils.logger import get_logger

logger = get_logger("import_worldcup")

BASE_URL = "https://raw.githubusercontent.com/openfootball/world-cup.json/master"

YEARS = [
    "1930", "1934", "1938", "1950", "1954", "1958",
    "1962", "1966", "1970", "1974", "1978", "1982",
    "1986", "1990", "1994", "1998", "2002", "2006",
    "2010", "2014", "2018", "2022",
]

# 淘汰赛轮次关键字（不含 Matchday/group/First round 等小组赛标识）
KNOCKOUT_ROUND_KEYWORDS = [
    "Round of 16", "Round of 32",
    "Quarter", "Semi", "Final",
    "Match for third place", "Match for 3rd",
    "Play-off", "Knockout",
]


def _is_knockout(round_name: str) -> bool:
    if not round_name:
        return False
    return any(kw.lower() in round_name.lower() for kw in KNOCKOUT_ROUND_KEYWORDS)


def _fetch_worldcup(year: str) -> Optional[list]:
    url = f"{BASE_URL}/{year}/worldcup.json"
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True)
        if r.status_code != 200:
            logger.warning(f"[{year}] HTTP {r.status_code}")
            return None
        data = r.json()
        matches = data.get("matches", [])
        logger.info(f"[{year}] Fetched {len(matches)} matches")
        return matches
    except Exception as e:
        logger.error(f"[{year}] Fetch failed: {e}")
        return None


def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _find_or_create_team(db: Session, name: str) -> Optional[int]:
    team = db.query(Team).filter(Team.name == name).first()
    if team:
        return team.id

    # 尝试通过 name_en 匹配
    team = db.query(Team).filter(Team.name_en == name).first()
    if team:
        return team.id

    # 创建新队伍
    code = name.upper().replace(" ", "_")[:10]
    team = Team(name=name, name_en=name, code=code)
    db.add(team)
    db.flush()
    logger.info(f"[team] Created: {name} (code={code})")
    return team.id


def _compute_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    elif home_goals == away_goals:
        return "draw"
    else:
        return "away"


def import_worldcup(
    db: Session,
    years: Optional[list] = None,
    dry_run: bool = False,
    knockout_only: bool = True,
) -> Dict:
    """导入世界杯数据"""
    target_years = years or YEARS
    stats = {"total": 0, "imported": 0, "skipped": 0, "knockout": 0}

    for year in target_years:
        matches = _fetch_worldcup(year)
        if not matches:
            continue

        competition = f"FIFA World Cup {year}"

        for m in matches:
            stats["total"] += 1
            round_name = m.get("round", "")
            team1 = m.get("team1", "")
            team2 = m.get("team2", "")
            date_str = m.get("date", "")

            if not team1 or not team2 or not date_str:
                stats["skipped"] += 1
                continue

            score = m.get("score") or {}
            ft = score.get("ft")
            if not ft or len(ft) < 2:
                stats["skipped"] += 1
                continue

            home_goals, away_goals = int(ft[0]), int(ft[1])
            kickoff = _parse_date(date_str)
            if not kickoff:
                stats["skipped"] += 1
                continue

            is_knockout = _is_knockout(round_name)

            if knockout_only and not is_knockout:
                stats["skipped"] += 1
                continue

            # 构建 match_code，保证唯一性
            match_code = f"WC{year}_{team1.upper()[:8]}_{team2.upper()[:8]}_{date_str.replace('-','')}"

            if dry_run:
                depth = " (淘汰赛)" if is_knockout else ""
                print(f"  [{year}] {team1} {home_goals}-{away_goals} {team2} [{round_name}]{depth}")
                stats["imported"] += 1
                if is_knockout:
                    stats["knockout"] += 1
                continue

            existing = db.query(Match).filter(Match.match_code == match_code).first()
            if existing:
                stats["skipped"] += 1
                continue

            home_id = _find_or_create_team(db, team1)
            away_id = _find_or_create_team(db, team2)
            if not home_id or not away_id:
                stats["skipped"] += 1
                continue

            actual_outcome = _compute_outcome(home_goals, away_goals)
            status = MatchStatus.FINISHED

            match = Match(
                match_code=match_code,
                home_team_id=home_id,
                away_team_id=away_id,
                kickoff_at=kickoff,
                status=status,
                match_type=MatchType.WORLD_CUP,
                competition=competition,
                stage=round_name,
                actual_home_goals=home_goals,
                actual_away_goals=away_goals,
                actual_outcome=actual_outcome,
            )
            db.add(match)
            stats["imported"] += 1
            if is_knockout:
                stats["knockout"] += 1

        if not dry_run:
            db.commit()
            logger.info(f"[{year}] Committed {stats['imported']} imported, {stats['skipped']} skipped")

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import World Cup historical data")
    parser.add_argument("--years", type=str, help="Comma-separated years (e.g. 2018,2022)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--all", action="store_true", help="Import ALL matches including group stage")
    args = parser.parse_args()

    years = args.years.split(",") if args.years else None
    knockout_only = not args.all

    db = SessionLocal()
    try:
        stats = import_worldcup(db, years=years, dry_run=args.dry_run, knockout_only=knockout_only)
        print(f"\n{'[DRY RUN]' if args.dry_run else '[IMPORT]'} Complete:")
        print(f"  Total matches read: {stats['total']}")
        print(f"  Imported: {stats['imported']}")
        print(f"  Skipped: {stats['skipped']}")
        print(f"  Of which knockout: {stats['knockout']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
