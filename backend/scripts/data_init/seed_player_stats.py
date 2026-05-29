"""
基于球队已有统计，生成代表性球员数据填充 player_stats 表。
每个队生成 5~8 名球员（前锋/中场/后卫/门将），xG/xA/minutes 与球队水平挂钩。
"""

import random
from sqlalchemy.orm import Session

from database.models import SessionLocal, Team, PlayerStats
from utils.logger import get_logger

logger = get_logger("seed_player_stats")
random.seed(42)

POSITIONS_WEIGHTS = {
    "FW": {"xg": 1.2, "xa": 0.5, "minutes": 0.75},
    "MF": {"xg": 0.4, "xa": 0.8, "minutes": 0.85},
    "DF": {"xg": 0.15, "xa": 0.2, "minutes": 0.9},
    "GK": {"xg": 0.01, "xa": 0.05, "minutes": 0.95},
}


def generate_players_for_team(team: Team) -> list:
    players = []
    count = random.randint(5, 8)

    for i in range(count):
        pos = random.choices(
            ["FW", "MF", "DF", "GK"],
            weights=[20, 35, 30, 15]
        )[0]
        w = POSITIONS_WEIGHTS[pos]

        # 球队 xG 越高，前锋 xG 越高
        base_xg = (team.avg_xg or 1.0) * w["xg"]
        base_xa = (team.avg_xg or 1.0) * w["xa"]

        # Elo 越高，球员质量越高
        quality = (team.elo or 1500) / 2000

        minutes = int(random.uniform(300, 900) * w["minutes"] * quality)
        goals = int(base_xg * quality * random.uniform(0.5, 2.0))
        assists = int(base_xa * quality * random.uniform(0.5, 2.0))
        xg = round(base_xg * quality * random.uniform(0.8, 1.5), 2)
        xa = round(base_xa * quality * random.uniform(0.8, 1.5), 2)
        shots = int(xg * random.uniform(8, 15))

        players.append({
            "team_id": team.id,
            "player_name": f"{team.code}-Player-{i+1}",
            "season": "2022",
            "league": "INT-World Cup",
            "minutes": minutes,
            "goals": goals,
            "assists": assists,
            "xg": xg,
            "xa": xa,
            "shots": shots,
            "key_passes": assists + random.randint(0, 5),
            "yellow_cards": random.randint(0, 3),
            "red_cards": random.randint(0, 1) if random.random() < 0.1 else 0,
            "source": "estimated",
        })

    return players


def seed_player_stats(db: Session) -> int:
    teams = db.query(Team).all()
    inserted = 0

    for team in teams:
        # 检查是否已有该队球员
        existing = db.query(PlayerStats).filter(PlayerStats.team_id == team.id).count()
        if existing > 1:
            continue

        players = generate_players_for_team(team)
        for p in players:
            ps = PlayerStats(**p)
            db.add(ps)
            inserted += 1

    db.commit()
    logger.info(f"[seed] Inserted {inserted} player stats rows for {len(teams)} teams")
    return inserted


if __name__ == "__main__":
    db = SessionLocal()
    try:
        count = seed_player_stats(db)
        print(f"Done. Inserted {count} rows.")
    finally:
        db.close()
