"""
基于现有基础字段（elo / avg_goals_scored / avg_goals_conceded）
推算并填充 teams 表的高级统计字段（xG / possession / pass_completion / shots_per_game）。

逻辑：
- xG/xGA 与场均进球/失强高度相关，用线性映射 + 随机扰动
- possession / pass_completion / shots_per_game 与 Elo 等级分正相关
- 这样 PoissonModel 和 TacticalModel 的新代码路径才能生效

运行后可通过 SQL 验证：SELECT name, avg_xg, possession FROM teams LIMIT 10;
"""

import math
import random
from sqlalchemy.orm import Session

from database.models import SessionLocal, Team
from utils.logger import get_logger

logger = get_logger("seed_advanced_stats")
random.seed(42)


def estimate_xg(avg_goals: float) -> float:
    """xG 通常比实际进球高 5~25%（包含浪费的好机会）"""
    if avg_goals <= 0:
        return 0.0
    noise = random.uniform(0.95, 1.25)
    return round(avg_goals * noise, 2)


def estimate_possession(elo: int) -> float:
    """Elo 与控球率强相关：顶尖球队 60%+，弱队 35%-"""
    if elo is None or elo <= 0:
        return 45.0
    # Elo 1500 -> 45%, 2000 -> 60%
    base = 30 + (elo / 2000) * 35
    noise = random.uniform(-3.0, 3.0)
    return round(max(30.0, min(70.0, base + noise)), 1)


def estimate_pass_completion(elo: int, possession: float) -> float:
    """传球成功率与 Elo 和控球率都正相关"""
    if elo is None or elo <= 0:
        return 72.0
    base = 65 + (elo / 2000) * 20 + (possession - 45) * 0.3
    noise = random.uniform(-2.0, 2.0)
    return round(max(55.0, min(92.0, base + noise)), 1)


def estimate_shots_per_game(avg_goals: float, elo: int) -> float:
    """射门数与进球能力和球队等级都相关"""
    if avg_goals <= 0:
        base = 8.0
    else:
        # 平均转化率约 10%，所以射门 ≈ 进球 * 10
        base = avg_goals * random.uniform(8, 14)
    # 强队创造机会更多
    if elo and elo > 1800:
        base += random.uniform(1.0, 3.0)
    return round(max(3.0, base), 1)


def seed_advanced_stats(db: Session) -> int:
    teams = db.query(Team).all()
    updated = 0

    for team in teams:
        changed = False

        if team.avg_xg is None or team.avg_xg == 0:
            team.avg_xg = estimate_xg(team.avg_goals_scored or 0)
            changed = True

        if team.avg_xga is None or team.avg_xga == 0:
            team.avg_xga = estimate_xg(team.avg_goals_conceded or 0)
            changed = True

        if team.possession is None or team.possession == 0:
            team.possession = estimate_possession(team.elo)
            changed = True

        if team.pass_completion is None or team.pass_completion == 0:
            team.pass_completion = estimate_pass_completion(team.elo, team.possession or 45)
            changed = True

        if team.shots_per_game is None or team.shots_per_game == 0:
            team.shots_per_game = estimate_shots_per_game(
                team.avg_goals_scored or 0, team.elo
            )
            changed = True

        if changed:
            updated += 1

    db.commit()
    logger.info(f"[seed] Updated advanced stats for {updated}/{len(teams)} teams")
    return updated


if __name__ == "__main__":
    db = SessionLocal()
    try:
        count = seed_advanced_stats(db)
        print(f"Done. Updated {count} teams.")
    finally:
        db.close()
