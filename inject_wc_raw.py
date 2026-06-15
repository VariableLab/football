
import sqlite3
from datetime import datetime

db_path = 'backend/database.sqlite'

def inject():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Matches to inject
    # match_code, home_id, away_id, group, stage, kickoff_at, status, match_type, competition, venue, odds_home, odds_draw, odds_away, odds_source
    new_matches = [
        ("WC2026-A1", 42, 17, "A", "group", "2026-06-11 18:00:00", "upcoming", "world_cup", "WC2026", "Estadio Azteca, Mexico City", 1.85, 3.40, 4.50, "synthetic"),
        ("WC2026-B1", 41, 49, "B", "group", "2026-06-11 20:00:00", "upcoming", "world_cup", "WC2026", "BMO Field, Toronto", 2.10, 3.20, 3.60, "synthetic"),
        ("WC2026-D1", 40, 68, "D", "group", "2026-06-12 19:00:00", "upcoming", "world_cup", "WC2026", "SoFi Stadium, Los Angeles", 1.95, 3.30, 4.00, "synthetic"),
        ("WC2026-C1", 18, 55, "C", "group", "2026-06-12 15:00:00", "upcoming", "world_cup", "WC2026", "MetLife Stadium, East Rutherford", 1.35, 4.80, 9.50, "synthetic"),
        ("WC2026-E1", 35, 1, "E", "group", "2026-06-13 13:00:00", "upcoming", "world_cup", "WC2026", "Hard Rock Stadium, Miami", 1.25, 5.50, 12.00, "synthetic"),
        ("WC2026-F1", 21, 2, "F", "group", "2026-06-13 16:00:00", "upcoming", "world_cup", "WC2026", "Mercedes-Benz Stadium, Atlanta", 1.55, 4.20, 6.50, "synthetic"),
        ("WC2026-CHN", 605, 34, "G", "group", "2026-06-14 19:00:00", "upcoming", "world_cup", "WC2026", "Rose Bowl, Pasadena", 8.50, 5.00, 1.35, "synthetic"),
    ]
    
    for m in new_matches:
        code = m[0]
        # Check if exists
        cursor.execute("SELECT id FROM matches WHERE match_code = ?", (code,))
        row = cursor.fetchone()
        if row:
            print(f"Updating match {code}...")
            cursor.execute("""
                UPDATE matches SET 
                home_team_id=?, away_team_id=?, "group"=?, stage=?, kickoff_at=?, status=?, match_type=?, competition=?, venue=?, odds_home=?, odds_draw=?, odds_away=?, odds_source=?
                WHERE match_code=?
            """, (m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], m[12], m[13], code))
        else:
            print(f"Inserting match {code}...")
            cursor.execute("""
                INSERT INTO matches (match_code, home_team_id, away_team_id, "group", stage, kickoff_at, status, match_type, competition, venue, odds_home, odds_draw, odds_away, odds_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, m)
            
    conn.commit()
    conn.close()
    print("Done.")

if __name__ == "__main__":
    inject()
