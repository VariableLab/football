"""
高级特征数据同步器

从多个数据源同步球队高级统计数据到 teams 表：
- avg_xg / avg_xga (期望进球/失球)
- possession (控球率)
- pass_completion (传球成功率)
- shots_per_game (场均射门)

数据源优先级:
1. FBref (soccerdata 库) - 免费爬虫，覆盖国际赛事
2. Elo 回归估算 - 无外部数据时的降级方案
3. 比赛数据推算 - 基于实际进球 + 对手 Elo 校准

用法:
    cd backend && python ingestion/feature_sync.py
    cd backend && python ingestion/feature_sync.py --dry-run
    cd backend && python ingestion/feature_sync.py --team ARG
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from database.models import SessionLocal, Team, Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("feature_sync")

# Elo -> xG 回归参数 (来自 xg_estimator.py)
ELO_XG_INTERCEPT = -2.17
ELO_XG_SLOPE = 0.00201
ELO_XGA_INTERCEPT = 5.03
ELO_XGA_SLOPE = -0.00218

LEAGUE_GOAL_FACTOR: Dict[str, float] = {
    "EPL": 1.05, "LaLiga": 0.95, "SerieA": 0.93, "Bundesliga": 1.08,
    "Ligue1": 0.97, "EFL": 1.02, "Eredivisie": 1.10, "J-League": 0.98,
    "K-League": 0.95, "Allsvenskan": 1.0,
}

CONTINENT_GOAL_FACTOR: Dict[str, float] = {
    "Europe": 1.0, "South_America": 0.95, "Asia": 0.98,
    "Africa": 0.92, "North_America": 1.02, "Oceania": 0.90,
}

# 默认值 (保守估计)
DEFAULT_XG = 1.20
DEFAULT_XGA = 1.10
DEFAULT_POSSESSION = 50.0
DEFAULT_PASS_COMPLETION = 78.0
DEFAULT_SHOTS_PER_GAME = 12.0


def elo_to_xg(elo: int, league: str = "", continent: str = "") -> float:
    base = ELO_XG_INTERCEPT + elo * ELO_XG_SLOPE
    factor = LEAGUE_GOAL_FACTOR.get(league, CONTINENT_GOAL_FACTOR.get(continent, 1.0))
    return max(round(base * factor, 2), 0.3)


def elo_to_xga(elo: int, league: str = "", continent: str = "") -> float:
    base = ELO_XGA_INTERCEPT + elo * ELO_XGA_SLOPE
    factor = LEAGUE_GOAL_FACTOR.get(league, CONTINENT_GOAL_FACTOR.get(continent, 1.0))
    return max(round(base * factor, 2), 0.3)


def elo_to_possession(elo: int) -> float:
    """Elo -> 控球率估算 (线性近似)"""
    return round(max(min(50 + (elo - 1500) * 0.015, 75), 30), 1)


def elo_to_pass_completion(elo: int) -> float:
    """Elo -> 传球成功率估算"""
    return round(max(min(70 + (elo - 1400) * 0.02, 88), 55), 1)


def elo_to_shots(elo: int) -> float:
    """Elo -> 场均射门估算"""
    return round(max(min(8 + (elo - 1400) * 0.015, 20), 5), 1)


def estimate_from_matches(team_id: int, db: Session, window: int = 20) -> Optional[Tuple[float, float]]:
    """从已完赛比赛推算 xG/xGA"""
    home_matches = (
        db.query(Match)
        .filter(Match.home_team_id == team_id, Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None), Match.actual_away_goals.isnot(None))
        .order_by(Match.kickoff_at.desc()).limit(window).all()
    )
    away_matches = (
        db.query(Match)
        .filter(Match.away_team_id == team_id, Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None), Match.actual_away_goals.isnot(None))
        .order_by(Match.kickoff_at.desc()).limit(window).all()
    )

    all_matches = home_matches + away_matches
    if len(all_matches) < 3:
        return None

    total_scored = total_conceded = total_opp_elo = count = 0
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return None

    team_elo = team.elo or 1500
    for m in all_matches:
        is_home = m.home_team_id == team_id
        scored = (m.actual_home_goals or 0) if is_home else (m.actual_away_goals or 0)
        conceded = (m.actual_away_goals or 0) if is_home else (m.actual_home_goals or 0)
        opp = db.query(Team).filter(Team.id == (m.away_team_id if is_home else m.home_team_id)).first()
        opp_elo = opp.elo if opp and opp.elo else 1500

        total_scored += scored
        total_conceded += conceded
        total_opp_elo += opp_elo
        count += 1

    if count == 0:
        return None

    avg_scored = total_scored / count
    avg_conceded = total_conceded / count
    avg_opp_elo = total_opp_elo / count
    elo_diff = team_elo - avg_opp_elo
    calibration = 1.0 + elo_diff * 0.00008

    xg = avg_scored / calibration
    xga = avg_conceded * calibration

    # 平滑: 0.7 实际 + 0.3 Elo
    elo_xg = elo_to_xg(team_elo)
    elo_xga = elo_to_xga(team_elo)
    xg = 0.7 * xg + 0.3 * elo_xg
    xga = 0.7 * xga + 0.3 * elo_xga

    return round(max(xg, 0.3), 2), round(max(xga, 0.3), 2)


def try_fbref_sync(db: Session, teams: List[Team]) -> Dict[str, Dict]:
    """
    尝试从 FBref (soccerdata) 获取真实 xG/xGA 数据。
    返回 {team_name: {avg_xg, avg_xga, possession, pass_completion, shots_per_game}}
    如果外部数据不可用，返回空字典。

    注意: FBref 使用 Selenium，首次加载较慢 (10-30s)。
    由于 macOS 不支持 SIGALRM，使用线程超时机制。
    """
    results: Dict[str, Dict] = {}
    try:
        from threading import Thread, Event

        fbref_exception: Optional[Exception] = None
        fbref_done = Event()

        def _fetch_fbref():
            nonlocal fbref_exception
            try:
                from soccerdata.fbref import FBref
                fb = FBref(
                    leagues=["Big 5 European Leagues Combined"],
                    seasons=["2025-2026"],
                    no_store=True,
                )
                table = fb.read_team_season_stats(stat_types=["performance", "advanced"])

                if table is None or table.empty:
                    logger.info("[fbref] No league table data returned")
                    return

                # 检查是否有 xG 相关列
                xg_cols = [c for c in table.columns if 'xg' in c.lower() or 'expected' in c.lower()]
                poss_cols = [c for c in table.columns if 'poss' in c.lower()]
                pass_cols = [c for c in table.columns if 'pass' in c.lower() and 'complet' in c.lower()]
                shot_cols = [c for c in table.columns if 'shot' in c.lower()]

                logger.info(f"[fbref] Available stat columns: xg={xg_cols}, poss={poss_cols}, pass={pass_cols}, shots={shot_cols}")

                for _, row in table.iterrows():
                    team_name = row.get('Team', row.get('team', ''))
                    if not team_name:
                        continue

                    entry = {}
                    if xg_cols:
                        entry['avg_xg'] = row.get(xg_cols[0])
                        entry['avg_xga'] = row.get(xg_cols[1] if len(xg_cols) > 1 else xg_cols[0])
                    if poss_cols:
                        entry['possession'] = row.get(poss_cols[0])
                    if pass_cols:
                        entry['pass_completion'] = row.get(pass_cols[0])
                    if shot_cols:
                        entry['shots_per_game'] = row.get(shot_cols[0])

                    if entry:
                        results[team_name] = entry

                logger.info(f"[fbref] Extracted data for {len(results)} teams")

            except Exception as e:
                fbref_exception = e

        thread = Thread(target=_fetch_fbref, daemon=True)
        thread.start()
        thread.join(timeout=25)

        if thread.is_alive():
            logger.info("[fbref] FBref sync timed out (25s), skipping Selenium-based scraping")
            results = {}
        elif fbref_exception:
            logger.info(f"[fbref] FBref error: {fbref_exception}, falling back to Elo estimation")
            results = {}

    except ImportError:
        logger.info("[fbref] soccerdata not installed, skipping FBref sync")
    except Exception as e:
        logger.info(f"[fbref] FBref unavailable ({e}), falling back to Elo estimation")

    return results


def sync_advanced_stats(db: Session, team_codes: Optional[List[str]] = None, dry_run: bool = False) -> Dict:
    """
    同步高级统计数据到 teams 表。

    流程:
    1. 尝试从 FBref 获取真实数据
    2. 对未获取到的球队，使用 Elo 回归估算
    3. 对有比赛数据的球队，用实际进球校准

    返回统计信息。
    """
    query = db.query(Team)
    if team_codes:
        query = query.filter(Team.code.in_([c.upper() for c in team_codes]))

    teams = query.all()
    stats = {
        "total": len(teams),
        "from_fbref": 0,
        "from_matches": 0,
        "from_elo": 0,
        "from_default": 0,
        "already_filled": 0,
        "errors": 0,
    }

    # Step 1: 尝试 FBref 数据
    fbref_data = try_fbref_sync(db, teams)

    for team in teams:
        try:
            # 已有完整数据
            if team.avg_xg and team.avg_xg > 0 and team.avg_xga and team.avg_xga > 0:
                stats["already_filled"] += 1
                continue

            updated = False

            # Step 2: 尝试 FBref 匹配
            fb_entry = None
            for fb_name, fb_val in fbref_data.items():
                if fb_name.lower() in (team.name or "").lower() or fb_name.lower() in (team.name_en or "").lower():
                    fb_entry = fb_val
                    break

            if fb_entry:
                if fb_entry.get('avg_xg'):
                    team.avg_xg = float(fb_entry['avg_xg'])
                    updated = True
                if fb_entry.get('avg_xga'):
                    team.avg_xga = float(fb_entry['avg_xga'])
                    updated = True
                if fb_entry.get('possession'):
                    team.possession = float(fb_entry['possession'])
                    updated = True
                if fb_entry.get('pass_completion'):
                    team.pass_completion = float(fb_entry['pass_completion'])
                    updated = True
                if fb_entry.get('shots_per_game'):
                    team.shots_per_game = float(fb_entry['shots_per_game'])
                    updated = True

            if updated:
                stats["from_fbref"] += 1
                logger.info(f"[feature_sync] {team.name}: filled from FBref xG={team.avg_xg}, poss={team.possession}")
                continue

            # Step 3: 从比赛数据推算
            match_result = estimate_from_matches(team.id, db)
            if match_result:
                team.avg_xg, team.avg_xga = match_result
                team.avg_goals_scored = round(match_result[0] * 1.05, 2)
                team.avg_goals_conceded = round(match_result[1] * 1.05, 2)
                stats["from_matches"] += 1
                logger.info(f"[feature_sync] {team.name}: xG={team.avg_xg}, xGA={team.avg_xga} from matches")
                updated = True

            # Step 4: Elo 回归估算
            if team.elo and team.elo > 0:
                if not team.avg_xg or team.avg_xg <= 0:
                    team.avg_xg = elo_to_xg(team.elo, "", team.continent or "")
                if not team.avg_xga or team.avg_xga <= 0:
                    team.avg_xga = elo_to_xga(team.elo, "", team.continent or "")
                if not team.possession or team.possession <= 0:
                    team.possession = elo_to_possession(team.elo)
                if not team.pass_completion or team.pass_completion <= 0:
                    team.pass_completion = elo_to_pass_completion(team.elo)
                if not team.shots_per_game or team.shots_per_game <= 0:
                    team.shots_per_game = elo_to_shots(team.elo)
                stats["from_elo"] += 1
                updated = True

            # Step 5: 默认值
            if not team.avg_xg or team.avg_xg <= 0:
                team.avg_xg = DEFAULT_XG
            if not team.avg_xga or team.avg_xga <= 0:
                team.avg_xga = DEFAULT_XGA
            if not team.possession or team.possession <= 0:
                team.possession = DEFAULT_POSSESSION
            if not team.pass_completion or team.pass_completion <= 0:
                team.pass_completion = DEFAULT_PASS_COMPLETION
            if not team.shots_per_game or team.shots_per_game <= 0:
                team.shots_per_game = DEFAULT_SHOTS_PER_GAME
            stats["from_default"] += 1

            team.stats_synced_at = datetime.now(timezone.utc)
            if updated or stats["from_default"] > 0:
                logger.info(f"[feature_sync] {team.name} ({team.code}): xG={team.avg_xg}, xGA={team.avg_xga}, poss={team.possession}")

        except Exception as e:
            logger.error(f"[feature_sync] Error syncing {team.name}: {e}")
            stats["errors"] += 1

    if not dry_run:
        db.commit()

    return stats


# --- CLI ---

def main():
    import argparse
    parser = argparse.ArgumentParser(description="高级特征数据同步器")
    parser.add_argument("--dry-run", action="store_true", help="仅输出不写入")
    parser.add_argument("--team", type=str, nargs="*", help="仅处理指定球队 code")
    args = parser.parse_args()

    print("=" * 60)
    print("  高级特征数据同步 (Feature Sync)")
    print("=" * 60)

    db = SessionLocal()
    try:
        t0 = time.time()

        team_codes = args.team
        stats = sync_advanced_stats(db, team_codes=team_codes, dry_run=args.dry_run)

        elapsed = time.time() - t0

        print(f"\n同步完成 ({elapsed:.1f}s):")
        print(f"  总球队数: {stats['total']}")
        print(f"  已有数据: {stats['already_filled']}")
        print(f"  FBref 数据: {stats['from_fbref']}")
        print(f"  比赛推算: {stats['from_matches']}")
        print(f"  Elo 估算: {stats['from_elo']}")
        print(f"  默认值: {stats['from_default']}")
        print(f"  错误: {stats['errors']}")

        # 覆盖率统计
        if stats['total'] > 0:
            for field in ['avg_xg', 'avg_xga', 'possession', 'pass_completion', 'shots_per_game']:
                count = db.query(Team).filter(
                    getattr(Team, field).isnot(None),
                    getattr(Team, field) != 0,
                ).count()
                pct = count / stats['total'] * 100
                print(f"  {field}: {count}/{stats['total']} = {pct:.0f}%")

    finally:
        db.close()


if __name__ == "__main__":
    main()
