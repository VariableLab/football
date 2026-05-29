#!/usr/bin/env python3
"""
xG 估算器 — 基于历史比赛数据与 Elo 评级估计球队 xG/xGA

策略：
1. 已有 xG 数据的球队保持不变
2. 有已完赛比赛数据的球队：从实际进球推算 xG（利用联赛平均进球率校准）
3. 无比赛数据的球队：基于 Elo 评级 + 联赛/地区基准推算 xG

Elo → xG 映射基于回归分析：
  avg_xg ≈ 1.0 + (elo - 1500) / 2000
  avg_xga ≈ 1.2 - (elo - 1500) / 2500

用法：
  python xg_estimator.py                  # 估算所有缺失 xG 的球队
  python xg_estimator.py --dry-run         # 仅输出不写入
  python xg_estimator.py --team ARG        # 仅处理指定球队
"""

from __future__ import annotations

import sys
import math
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from database.models import SessionLocal, Team, Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("xg_estimator")

# ────────────────────────────
# Elo → xG 回归参数
# ────────────────────────────
# 基于 94 支有真实 xG 数据的球队回归拟合
# xG = -2.17 + 0.00201 * elo  (R² ≈ 0.86)
# xGA = 5.03 - 0.00218 * elo   (R² ≈ 0.83)
ELO_XG_INTERCEPT = -2.17
ELO_XG_SLOPE = 0.00201
ELO_XGA_INTERCEPT = 5.03
ELO_XGA_SLOPE = -0.00218

# 联赛/地区进球率校准系数（相对全球平均的倍率）
LEAGUE_GOAL_FACTOR: Dict[str, float] = {
    "EPL": 1.05,       # 英超进球偏多
    "LaLiga": 0.95,    # 西甲进球偏少
    "SerieA": 0.93,    # 意甲防守强
    "Bundesliga": 1.08, # 德甲进球多
    "Ligue1": 0.97,    # 法甲
    "EFL": 1.02,       # 英冠
    "Eredivisie": 1.10, # 荷甲进球多
    "J-League": 0.98,  # J联赛
    "K-League": 0.95,  # K联赛
    "Allsvenskan": 1.0, # 瑞典超
}

# 大洲基准进球率
CONTINENT_GOAL_FACTOR: Dict[str, float] = {
    "Europe": 1.0,
    "South_America": 0.95,
    "Asia": 0.98,
    "Africa": 0.92,
    "North_America": 1.02,
    "Oceania": 0.90,
}

# 默认联赛校准
DEFAULT_LEAGUE_FACTOR = 1.0


def elo_to_xg(elo: int, league: str = "", continent: str = "") -> float:
    """基于 Elo 估算场均 xG（回归校准版）"""
    base = ELO_XG_INTERCEPT + elo * ELO_XG_SLOPE
    factor = LEAGUE_GOAL_FACTOR.get(league, CONTINENT_GOAL_FACTOR.get(continent, DEFAULT_LEAGUE_FACTOR))
    return max(base * factor, 0.3)


def elo_to_xga(elo: int, league: str = "", continent: str = "") -> float:
    """基于 Elo 估算场均 xGA（回归校准版）"""
    base = ELO_XGA_INTERCEPT + elo * ELO_XGA_SLOPE
    factor = LEAGUE_GOAL_FACTOR.get(league, CONTINENT_GOAL_FACTOR.get(continent, DEFAULT_LEAGUE_FACTOR))
    return max(base * factor, 0.3)


def estimate_from_matches(
    team_id: int,
    db: Session,
    window: int = 20,
) -> Optional[Tuple[float, float]]:
    """
    从已完赛比赛推算 xG：
    使用实际进球率，但考虑对手 Elo 进行校准。
    返回 (xg, xga) 或 None（无足够数据）。
    """
    # 获取该队已完赛比赛（主客场均包含）
    home_matches = (
        db.query(Match)
        .filter(
            Match.home_team_id == team_id,
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.actual_away_goals.isnot(None),
        )
        .order_by(Match.kickoff_at.desc())
        .limit(window)
        .all()
    )
    away_matches = (
        db.query(Match)
        .filter(
            Match.away_team_id == team_id,
            Match.status == MatchStatus.FINISHED,
            Match.actual_home_goals.isnot(None),
            Match.actual_away_goals.isnot(None),
        )
        .order_by(Match.kickoff_at.desc())
        .limit(window)
        .all()
    )

    all_matches = home_matches + away_matches
    if len(all_matches) < 3:
        return None

    total_goals_scored = 0.0
    total_goals_conceded = 0.0
    total_opponent_elo = 0.0
    count = 0

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return None

    team_elo = team.elo or 1500

    for m in all_matches:
        is_home = m.home_team_id == team_id
        if is_home:
            goals_scored = m.actual_home_goals or 0
            goals_conceded = m.actual_away_goals or 0
            opp = db.query(Team).filter(Team.id == m.away_team_id).first()
        else:
            goals_scored = m.actual_away_goals or 0
            goals_conceded = m.actual_home_goals or 0
            opp = db.query(Team).filter(Team.id == m.home_team_id).first()

        opp_elo = opp.elo if opp and opp.elo else 1500
        total_goals_scored += goals_scored
        total_goals_conceded += goals_conceded
        total_opponent_elo += opp_elo
        count += 1

    if count == 0:
        return None

    avg_scored = total_goals_scored / count
    avg_conceded = total_goals_conceded / count
    avg_opp_elo = total_opponent_elo / count

    # 基于对手平均 Elo 校准：对手越强，实际进球越接近 xG
    # 对手偏弱（Elo < 1500）→ 实际进球偏多 → xG 向下修正
    # 对手偏强（Elo > 1500）→ 实际进球偏少 → xG 向上修正
    elo_diff = team_elo - avg_opp_elo
    calibration = 1.0 + elo_diff * 0.00008  # 微调

    xg = avg_scored / calibration
    xga = avg_conceded * calibration

    # 平滑：与 Elo 推算值取加权平均（0.7 实际 + 0.3 Elo）
    elo_xg = elo_to_xg(team_elo)
    elo_xga = elo_to_xga(team_elo)
    xg = 0.7 * xg + 0.3 * elo_xg
    xga = 0.7 * xga + 0.3 * elo_xga

    return round(max(xg, 0.3), 2), round(max(xga, 0.3), 2)


def fill_missing_xg(db: Session, dry_run: bool = False, team_code: str = None) -> Dict:
    """
    为所有缺失 xG/xGA 的球队填充估算值。

    优先级：
    1. 已有 avg_xg > 0 → 跳过
    2. 有 ≥3 场已完赛比赛 → 从实际进球推算
    3. Elo 评级可用 → Elo 回归推算
    4. 无任何数据 → 使用保守默认值 (1.20, 1.10)
    """
    query = db.query(Team)
    if team_code:
        query = query.filter(Team.code == team_code.upper())

    teams = query.all()
    stats = {"skipped": 0, "from_matches": 0, "from_elo": 0, "from_default": 0, "errors": 0}

    for team in teams:
        # 已有数据，跳过
        if team.avg_xg and team.avg_xg > 0:
            stats["skipped"] += 1
            continue

        xg, xga = None, None
        source = ""

        # 策略 1: 从比赛数据推算
        result = estimate_from_matches(team.id, db)
        if result:
            xg, xga = result
            source = "matches"
        # 策略 2: 从 Elo 推算
        elif team.elo and team.elo > 0:
            # 尝试推断联赛
            league = ""
            if team.name_en:
                for lg in LEAGUE_GOAL_FACTOR:
                    if lg.lower() in (team.name_en or "").lower():
                        league = lg
                        break
            xg = elo_to_xg(team.elo, league, team.continent or "")
            xga = elo_to_xga(team.elo, league, team.continent or "")
            source = "elo"
        # 策略 3: 默认值
        else:
            xg = 1.20
            xga = 1.10
            source = "default"

        if xg and xga:
            if dry_run:
                logger.info(f"[DRY-RUN] {team.name} ({team.code}): xG={xg:.2f}, xGA={xga:.2f} ← {source}")
            else:
                team.avg_xg = xg
                team.avg_xga = xga
                if source == "matches":
                    # 同时更新实际进球率
                    team.avg_goals_scored = xg * 1.05  # 实际进球略高于 xG
                    team.avg_goals_conceded = xga * 1.05
                logger.info(f"{team.name} ({team.code}): xG={xg:.2f}, xGA={xga:.2f} ← {source}")

            stats[f"from_{source}"] += 1
        else:
            stats["errors"] += 1

    if not dry_run:
        db.commit()

    return stats


# ────────────────────────────
# CLI
# ────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="xG 估算器")
    parser.add_argument("--dry-run", action="store_true", help="仅输出不写入")
    parser.add_argument("--team", type=str, help="仅处理指定球队 code")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = fill_missing_xg(db, dry_run=args.dry_run, team_code=args.team)
        logger.info(f"完成: {stats}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
