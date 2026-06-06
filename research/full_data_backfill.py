import os
import sys
from pathlib import Path
from datetime import datetime

# --- 关键：必须在任何模块导入前设置环境变量 ---
os.environ["SECRET_KEY"] = "mJQbCMDdRgOArjxCq6tFt_c0Pye2zyS9CiqlkmkMq8skb0KKQMr3k0jfm-2Dga0M"
os.environ["ADMIN_API_KEY"] = "mJQbCMDdRgOArjxCq6tFt_c0Pye2zyS9CiqlkmkMq8skb0KKQMr3k0jfm-2Dga0M"

# 设置路径
_research_root = Path(__file__).resolve().parent
_backend_root = _research_root.parent / "backend"
sys.path.append(str(_backend_root))

from database.models import SessionLocal, Team
from integrations.soccerdata_adapter import SoccerDataSync

def run_backfill():
    print(f"🚀 [{datetime.now()}] 重新启动全量数据补全任务...")
    db = SessionLocal()
    sync = SoccerDataSync(db)
    
    # 补全联赛列表
    LEAGUES = [
        ("INT-World Cup", "2022"),
        ("ESP-La Liga", "2324"),
        ("ENG-Premier League", "2324"),
        ("GER-Bundesliga", "2324"),
        ("ITA-Serie A", "2324"),
        ("FRA-Ligue 1", "2324")
    ]
    
    try:
        # 1. 同步全球 Elo
        print("📊 [1/3] 同步全球俱乐部 Elo 等级分...")
        sync.sync_elo_ratings()
        
        # 2. 补全球队统计
        for league, season in LEAGUES:
            print(f"📈 [2/3] 同步 {league} {season} 球队深度统计...")
            sync.sync_fbref_team_stats(league=league, season=season)
            
        # 3. 补全球员统计
        for league, season in LEAGUES:
            print(f"👤 [3/3] 同步 {league} {season} 核心球员数据...")
            sync.sync_fbref_player_stats(league=league, season=season)
            
        print(f"✅ [{datetime.now()}] 数据补全任务执行完毕。")
        
    except Exception as e:
        print(f"❌ 补全过程中止: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_backfill()
