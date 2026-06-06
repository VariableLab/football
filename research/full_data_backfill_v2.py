import os
import sys
import time
from pathlib import Path
from datetime import datetime

os.environ["SECRET_KEY"] = "mJQbCMDdRgOArjxCq6tFt_c0Pye2zyS9CiqlkmkMq8skb0KKQMr3k0jfm-2Dga0M"
os.environ["ADMIN_API_KEY"] = "mJQbCMDdRgOArjxCq6tFt_c0Pye2zyS9CiqlkmkMq8skb0KKQMr3k0jfm-2Dga0M"

_research_root = Path(__file__).resolve().parent
_backend_root = _research_root.parent / "backend"
sys.path.append(str(_backend_root))

from database.models import SessionLocal
from integrations.soccerdata_adapter import SoccerDataSync

def run_backfill():
    print(f"🚀 [{datetime.now()}] 启动 V2 版稳健补全任务...")
    db = SessionLocal()
    sync = SoccerDataSync(db)
    
    # 缩小范围，优先确保最核心的赛事有数据
    LEAGUES = [
        ("INT-World Cup", "2022"),
        ("ENG-Premier League", "2324"),
        ("ESP-La Liga", "2324")
    ]
    
    try:
        # 1. Elo 优先 (ClubElo 接口通常不依赖 Chrome，很稳)
        print("📊 [1/3] 同步全球 Elo 等级分...")
        sync.sync_elo_ratings()
        
        # 2. 球队与球员统计 (按联赛顺序执行，每完成一个休息 30 秒防止被封)
        for league, season in LEAGUES:
            print(f"📈 正在处理: {league} {season}...")
            try:
                # 尝试抓取球队统计
                sync.sync_fbref_team_stats(league=league, season=season)
                time.sleep(10)
                # 尝试抓取球员统计
                sync.sync_fbref_player_stats(league=league, season=season)
                print(f"✅ {league} 同步成功")
            except Exception as e:
                print(f"⚠️ {league} 同步异常 (已跳过): {e}")
            
            time.sleep(30) # 冷却
            
        print(f"✅ [{datetime.now()}] 稳健版补全任务结束。")
        
    finally:
        db.close()

if __name__ == "__main__":
    run_backfill()
