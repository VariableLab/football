import sqlite3
import sys
import os

# Ensure we can import from backend

from core.prediction_engine import PredictionEngine, TeamContext, MatchContext

def test_raw_sqlite():
    db_path = "database.sqlite"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 模拟简单的 PredictionEngine 调用环境
    from database.config import get_settings
    settings = get_settings()
    
    engine = PredictionEngine()
    target_leagues = ["EPL", "LaLiga", "SerieA", "Bundesliga"]
    
    print("🚀 开始测试 '分联赛垂直权重' 的实战表现...")
    
    for league in target_leagues:
        query = """
            SELECT m.id, m.competition, m.actual_outcome, 
                   t1.name, t1.elo, t1.fifa_rank, t1.avg_goals_scored, t1.avg_goals_conceded,
                   t2.name, t2.elo, t2.fifa_rank, t2.avg_goals_scored, t2.avg_goals_conceded,
                   m.odds_home, m.odds_draw, m.odds_away
            FROM matches m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE m.competition = ? AND m.status = 'FINISHED' AND m.actual_outcome IS NOT NULL
            LIMIT 100
        """
        cursor.execute(query, (league,))
        rows = cursor.fetchall()
        
        if not rows:
            print(f"  - {league:12}: 无匹配数据")
            continue
            
        correct = 0
        total = 0
        for r in rows:
            try:
                mid, comp, actual, hname, helo, hrank, hgs, hgc, aname, aelo, arank, ags, agc, oh, od, oa = r
                
                h_ctx = TeamContext(team_id=1, name=hname, elo=helo or 1500, fifa_rank=hrank or 100, 
                                    avg_goals_scored=hgs or 1.3, avg_goals_conceded=hgc or 1.3)
                a_ctx = TeamContext(team_id=2, name=aname, elo=aelo or 1500, fifa_rank=arank or 100, 
                                    avg_goals_scored=ags or 1.3, avg_goals_conceded=agc or 1.3)
                ctx = MatchContext(match_id=mid, home_team=h_ctx, away_team=a_ctx, competition=comp, 
                                   odds_home=oh, odds_draw=od, odds_away=oa)
                
                res = engine.predict(ctx)
                pred_outcome = max(res.spf, key=res.spf.get)
                if pred_outcome == actual:
                    correct += 1
                total += 1
            except:
                continue
                
        acc = correct / total if total > 0 else 0
        print(f"  - {league:12}: {acc:6.1%} ({correct}/{total})")
    
    conn.close()

if __name__ == "__main__":
    test_raw_sqlite()
