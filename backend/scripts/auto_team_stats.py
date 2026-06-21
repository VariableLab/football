import sys
import os
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(_root)

from database.models import SessionLocal, Team, Match, MatchStatus
from datetime import datetime, timezone

def calculate_dynamic_stats():
    db = SessionLocal()
    print("🚀 启动全自动球队战力推演 (Safe Version)...")
    
    teams = db.query(Team).all()
    updated = 0
    
    # 针对 PostgreSQL 的 Enum 映射，直接在 Python 层过滤提高兼容性
    for team in teams:
        recent_matches = db.query(Match).filter(
            ((Match.home_team_id == team.id) | (Match.away_team_id == team.id)),
            Match.status == MatchStatus.FINISHED
        ).order_by(Match.kickoff_at.desc()).limit(10).all()
        
        if not recent_matches:
            continue
            
        total_scored = 0
        total_conceded = 0
        for m in recent_matches:
            if m.home_team_id == team.id:
                total_scored += (m.actual_home_goals or 0)
                total_conceded += (m.actual_away_goals or 0)
            else:
                total_scored += (m.actual_away_goals or 0)
                total_conceded += (m.actual_home_goals or 0)
        
        count = len(recent_matches)
        team.avg_goals_scored = round(total_scored / count, 2)
        team.avg_goals_conceded = round(total_conceded / count, 2)
        team.stats_synced_at = datetime.now(timezone.utc)
        updated += 1
        
        if updated % 100 == 0:
            print(f"  - Updated {updated} teams...")

    db.commit()
    print(f"✅ 完成。共更新 {updated} 支球队战力画像。")
    db.close()

if __name__ == "__main__":
    calculate_dynamic_stats()
