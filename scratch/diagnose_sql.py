import os
from sqlalchemy import create_engine, text

def diagnose():
    db_path = os.path.expanduser('~/Github/football/backend/database.sqlite')
    db_url = f'sqlite:///{db_path}'
    print(f"Connecting to SQLite: {db_url}")
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # 1. 查找 home_team_id 在 teams 表中不存在的比赛
        query_home = text("""
            SELECT m.id, m.match_code, m.home_team_id 
            FROM matches m
            LEFT JOIN teams t ON m.home_team_id = t.id
            WHERE t.id IS NULL AND m.home_team_id IS NOT NULL
        """)
        res_home = conn.execute(query_home).fetchall()
        print(f"Orphan Home Teams in matches: {len(res_home)}")
        for r in res_home:
            print(f"  Match ID: {r[0]} | Code: {r[1]} | home_team_id: {r[2]}")
            
        # 2. 查找 away_team_id 在 teams 表中不存在的比赛
        query_away = text("""
            SELECT m.id, m.match_code, m.away_team_id 
            FROM matches m
            LEFT JOIN teams t ON m.away_team_id = t.id
            WHERE t.id IS NULL AND m.away_team_id IS NOT NULL
        """)
        res_away = conn.execute(query_away).fetchall()
        print(f"Orphan Away Teams in matches: {len(res_away)}")
        for r in res_away:
            print(f"  Match ID: {r[0]} | Code: {r[1]} | away_team_id: {r[2]}")

if __name__ == "__main__":
    diagnose()
