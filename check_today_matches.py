
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Set up path to include backend
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from database.config import get_settings
from database.models import Match, Team, MatchStatus

settings = get_settings()
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def check_matches():
    print(f"Checking matches for date: 2026-06-11 (Database: {settings.DATABASE_URL})")
    
    # Query matches on 2026-06-11
    # Note: date() function might vary by SQL dialect, but for SQLite it works
    matches = db.query(Match).filter(
        text("date(kickoff_at) = '2026-06-11'")
    ).all()
    
    # Check any matches in June or July 2026
    summer_matches = db.query(Match).filter(
        text("date(kickoff_at) >= '2026-06-01'"),
        text("date(kickoff_at) <= '2026-07-31'")
    ).order_by(Match.kickoff_at).all()
    
    print(f"Total matches in June/July 2026: {len(summer_matches)}")
    for m in summer_matches:
        home = db.get(Team, m.home_team_id)
        away = db.get(Team, m.away_team_id)
        print(f"[{m.status}] {m.match_code}: {home.name} vs {away.name} at {m.kickoff_at} (Stage: {m.stage}, Type: {m.match_type})")

if __name__ == "__main__":
    check_matches()
    db.close()
