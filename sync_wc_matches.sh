
#!/bin/bash
DB="backend/database.sqlite"

echo "Updating World Cup Matches for 2026-06-11..."

# Mexico vs South Africa
sqlite3 $DB "UPDATE matches SET home_team_id=42, away_team_id=17, kickoff_at='2026-06-11 18:00:00', status='upcoming', venue='Estadio Azteca', odds_home=1.85, odds_draw=3.40, odds_away=4.50 WHERE match_code='WC2026-A1';"

# Canada vs Ireland
sqlite3 $DB "UPDATE matches SET home_team_id=41, away_team_id=49, kickoff_at='2026-06-11 20:00:00', status='upcoming', venue='BMO Field', odds_home=2.10, odds_draw=3.20, odds_away=3.60 WHERE match_code='WC2026-B1';" || \
sqlite3 $DB "INSERT INTO matches (match_code, home_team_id, away_team_id, 'group', stage, kickoff_at, status, match_type, competition, venue, odds_home, odds_draw, odds_away, odds_source) VALUES ('WC2026-B1', 41, 49, 'B', 'group', '2026-06-11 20:00:00', 'upcoming', 'world_cup', 'WC2026', 'BMO Field', 2.10, 3.20, 3.60, 'synthetic');"

# USA vs Wales
sqlite3 $DB "UPDATE matches SET home_team_id=40, away_team_id=68, kickoff_at='2026-06-12 19:00:00', status='upcoming', venue='SoFi Stadium', odds_home=1.95, odds_draw=3.30, odds_away=4.00 WHERE match_code='WC2026-D1';" || \
sqlite3 $DB "INSERT INTO matches (match_code, home_team_id, away_team_id, 'group', stage, kickoff_at, status, match_type, competition, venue, odds_home, odds_draw, odds_away, odds_source) VALUES ('WC2026-D1', 40, 68, 'D', 'group', '2026-06-12 19:00:00', 'upcoming', 'world_cup', 'WC2026', 'SoFi Stadium', 1.95, 3.30, 4.00, 'synthetic');"

# China vs Argentina
sqlite3 $DB "INSERT INTO matches (match_code, home_team_id, away_team_id, 'group', stage, kickoff_at, status, match_type, competition, venue, odds_home, odds_draw, odds_away, odds_source) VALUES ('WC2026-CHN', 605, 34, 'G', 'group', '2026-06-14 19:00:00', 'upcoming', 'world_cup', 'WC2026', 'Rose Bowl', 8.50, 5.00, 1.35, 'synthetic');"

echo "Sync complete."
