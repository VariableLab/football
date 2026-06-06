import sys
import os
import json
from datetime import datetime, timedelta, timezone

# 确保导入路径正确 (针对服务器环境)
PROJECT_ROOT = "/home/ubuntu/Github/football"
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))
sys.path.append(os.path.join(PROJECT_ROOT, "research", "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.models import Match, MatchStatus
from footy.content.engine import WorldCupContentEngine
from footy.evaluation.visualizer import MatchCardGenerator
from footy.models.poisson import PoissonPredictor

def run_auto_previews():
    """
    全自动前瞻生成任务：
    1. 扫描数据库中即将开始的比赛
    2. 使用实验室专家模型进行预测
    3. 渲染高级战术前瞻卡片
    """
    print(f"🚀 [{datetime.now()}] 启动全自动前瞻生成引擎...")
    
    # 路径配置
    OUTPUT_DIR = os.path.join(PROJECT_ROOT, "static", "research_gallery")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 加载权重
    WEIGHT_PATH = os.path.join(PROJECT_ROOT, "backend", "data", "weights", "research", "poisson_expert_weights.json")
    model = PoissonPredictor()
    model.load_params(WEIGHT_PATH)
    
    # 数据库连接
    from database.config import get_settings
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # 查找未来 48 小时内的比赛 (包含模拟的世界杯赛程)
        now = datetime.now(timezone.utc)
        future_48h = now + timedelta(hours=48)
        
        matches = db.query(Match).filter(
            Match.kickoff_at >= now,
            Match.kickoff_at <= future_48h
        ).all()

        print(f"  - 发现 {len(matches)} 场待生成赛事。")

        viz = MatchCardGenerator(output_dir=OUTPUT_DIR)
        
        for m in matches:
            print(f"    - 正在处理: {m.home_team.name} vs {m.away_team.name}...")
            
            # 1. 执行预测
            import pandas as pd
            df_mock = pd.DataFrame([{"HomeTeam": m.home_team.name, "AwayTeam": m.away_team.name}])
            probs = model.predict_proba(df_mock)[0]
            
            # 2. 构造数据结构 (适配高级卡片渲染)
            card_data = {
                "match_info": {
                    "pairing": f"{m.home_team.name} vs {m.away_team.name}",
                    "venue": m.venue or "2026 World Cup Stadium"
                },
                "stats": {
                    "avg_xg": {"home": 1.45, "away": 1.25}, # 简化处理
                    "h2h": "Historical Encounter"
                },
                "ai_analysis": {
                    "content": f"Tactical analysis for {m.home_team.name} vs {m.away_team.name} powered by WC-Analytics Lab."
                },
                "prediction_ref": {
                    "home_win": probs[0],
                    "draw": probs[1],
                    "away_win": probs[2]
                }
            }
            
            # 3. 渲染高级卡片 (带热图)
            img_path = viz.generate_advanced_card(card_data)
            print(f"      ✅ 卡片已保存: {img_path}")

    finally:
        db.close()

if __name__ == "__main__":
    run_auto_previews()
