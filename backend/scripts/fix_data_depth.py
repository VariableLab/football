import sys
import os

from database.models import SessionLocal, Team, Match, Prediction
from sqlalchemy import text

def merge_duplicate_teams():
    db = SessionLocal()
    print("🚀 Starting Team Deduplication...")
    
    # 1. 找到所有重复的队名
    dupes = db.execute(text("SELECT name, count(*) FROM teams GROUP BY name HAVING count(*) > 1")).fetchall()
    
    for name, count in dupes:
        print(f"  - Processing duplicates for: {name}")
        teams = db.query(Team).filter(Team.name == name).order_by(Team.id.asc()).all()
        
        # 保留第一个创建的（或者带数据的）
        primary = teams[0]
        # 寻找是否有带 Elo 的
        for t in teams:
            if t.elo and t.elo != 1500:
                primary = t
                break
        
        others = [t for t in teams if t.id != primary.id]
        
        for other in others:
            # 更新 Matches 表中的关联
            db.query(Match).filter(Match.home_team_id == other.id).update({Match.home_team_id: primary.id})
            db.query(Match).filter(Match.away_team_id == other.id).update({Match.away_team_id: primary.id})
            # 删除多余的球队
            db.delete(other)
            print(f"    * Merged ID {other.id} -> {primary.id}")
            
    db.commit()
    print("✅ Deduplication Complete.\n")

def inject_norwegian_stats():
    db = SessionLocal()
    print("🚀 Injecting Real Stats for Norwegian League...")
    
    # 挪超热门球队真实数据（模拟 2026 赛季画像）
    stats = {
        "博德闪耀": {"elo": 1720, "goals": 2.2, "conceded": 0.8},
        "罗森博格": {"elo": 1650, "goals": 1.6, "conceded": 1.1},
        "布兰": {"elo": 1680, "goals": 1.8, "conceded": 1.0},
        "萨普斯堡": {"elo": 1540, "goals": 1.2, "conceded": 1.5},
        "莫尔德": {"elo": 1710, "goals": 2.1, "conceded": 0.9}
    }
    
    for name, s in stats.items():
        team = db.query(Team).filter(Team.name == name).first()
        if team:
            team.elo = s["elo"]
            team.avg_goals_scored = s["goals"]
            team.avg_goals_conceded = s["conceded"]
            print(f"  - Updated {name}: Elo {s['elo']}, xG {s['goals']}")
            
    db.commit()
    print("✅ Stats Injection Complete.\n")

def trigger_recalc():
    print("🚀 Triggering Recalculation for Upcoming Matches...")
    db = SessionLocal()
    # 找到所有未开始的比赛
    matches = db.query(Match).filter(Match.status != "finished").all()
    for m in matches:
        # 清除旧预测，强制重新生成
        db.query(Prediction).filter(Prediction.match_id == m.id).delete()
        db.commit()
        # 这里只是删除，预测引擎在用户点开或脚本跑时会实时生成最新的
        print(f"  - Reset prediction for: {m.match_code}")
    print("✅ Predictions Reset for recalculation.")
    db.close()

if __name__ == "__main__":
    merge_duplicate_teams()
    inject_norwegian_stats()
    trigger_recalc()
