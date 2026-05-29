"""
从 matches 表已有赔率 + 模拟多庄家差异，填充 match_bookmaker_odds 表。
让 OddsHarvester / 赔率融合链路有数据可消费。
"""

import random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from database.models import SessionLocal, Match, MatchBookmakerOdds
from utils.logger import get_logger

logger = get_logger("seed_bookmaker_odds")
random.seed(42)

# 模拟几个主流庄家，赔率有轻微差异（反映 margin 不同）
BOOKMAKERS = [
    ("bet365", 0.97),
    ("pinnacle", 0.98),
    ("williamhill", 0.96),
    ("betfair", 0.975),
]


def vary_odds(base: float, factor: float) -> float:
    """根据庄家 margin factor 微调赔率"""
    if base is None or base <= 0:
        return 0.0
    noise = random.uniform(-0.05, 0.05)
    return round(base * factor + noise, 2)


def seed_bookmaker_odds(db: Session) -> int:
    matches = db.query(Match).all()
    inserted = 0

    for m in matches:
        # 只处理有赔率的场次
        if not (m.odds_home and m.odds_draw and m.odds_away):
            continue

        recorded = m.kickoff_at or datetime.utcnow()

        for name, factor in BOOKMAKERS:
            # 避免重复
            existing = db.query(MatchBookmakerOdds).filter(
                MatchBookmakerOdds.match_id == m.id,
                MatchBookmakerOdds.bookmaker == name,
            ).first()
            if existing:
                continue

            bo = MatchBookmakerOdds(
                match_id=m.id,
                bookmaker=name,
                odds_home=vary_odds(m.odds_home, factor),
                odds_draw=vary_odds(m.odds_draw, factor),
                odds_away=vary_odds(m.odds_away, factor),
                recorded_at=recorded,
                is_closing=False,
            )
            db.add(bo)
            inserted += 1

    db.commit()
    logger.info(f"[seed] Inserted {inserted} bookmaker odds rows for {len(matches)} matches")
    return inserted


if __name__ == "__main__":
    db = SessionLocal()
    try:
        count = seed_bookmaker_odds(db)
        print(f"Done. Inserted {count} rows.")
    finally:
        db.close()
