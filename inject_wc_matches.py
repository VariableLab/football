
import os
import sys
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend subdirectories to path
_root = os.path.join(os.getcwd(), 'backend')
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

from database.config import get_settings
from database.models import Match, Team, MatchStatus, MatchType, Prediction, PlayType
from core.prediction_engine import PredictionEngine, MatchContext, TeamContext

settings = get_settings()
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def inject_wc_matches():
    print(f"Injecting 2026 World Cup opening matches into {settings.DATABASE_URL}...")
    
    # Matches to inject
    # format: (match_code, home_id, away_id, group, stage, kickoff_iso)
    new_matches = [
        ("WC2026-A1", 42, 17, "A", "group", "2026-06-11T18:00:00Z"), # Mexico vs South Africa
        ("WC2026-B1", 41, 49, "B", "group", "2026-06-11T20:00:00Z"), # Canada vs Ireland
        ("WC2026-D1", 40, 68, "D", "group", "2026-06-12T19:00:00Z"), # USA vs Wales
        ("WC2026-C1", 18, 55, "C", "group", "2026-06-12T15:00:00Z"), # France vs Finland
        ("WC2026-E1", 35, 1, "E", "group", "2026-06-13T13:00:00Z"),  # Brazil vs Japan
        ("WC2026-F1", 21, 2, "F", "group", "2026-06-13T16:00:00Z"),  # Germany vs South Korea
        ("WC2026-CHN", 605, 34, "G", "group", "2026-06-14T19:00:00Z"), # China vs Argentina (Special test case)
    ]
    
    for code, h_id, a_id, grp, stage, kickoff in new_matches:
        # Check if exists
        existing = db.query(Match).filter(Match.match_code == code).first()
        if existing:
            print(f"Match {code} already exists, updating...")
            existing.home_team_id = h_id
            existing.away_team_id = a_id
            existing.group = grp
            existing.stage = stage
            existing.kickoff_at = datetime.fromisoformat(kickoff.replace('Z', '+00:00'))
            existing.status = MatchStatus.UPCOMING
            match_obj = existing
        else:
            print(f"Creating match {code}...")
            match_obj = Match(
                match_code=code,
                home_team_id=h_id,
                away_team_id=a_id,
                group=grp,
                stage=stage,
                kickoff_at=datetime.fromisoformat(kickoff.replace('Z', '+00:00')),
                status=MatchStatus.UPCOMING,
                match_type=MatchType.WORLD_CUP,
                competition="WC2026",
                venue="2026 World Cup Stadium",
                odds_home=2.0, odds_draw=3.4, odds_away=3.8, # Placeholder odds
                odds_source="synthetic"
            )
            db.add(match_obj)
    
    db.commit()
    print("Injection complete.")
    
    # Optional: Run prediction in a separate pass if needed

if __name__ == "__main__":
    inject_wc_matches()
    db.close()
