
import sys
import os

# 将 backend 加入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import SessionLocal, Match, Prediction, MatchStatus
from ingestion.data_cleaner import DataCleaner
from core.prediction_engine import PredictionEngine, build_context_from_match

def main():
    # 💡 强力加载本地环境配置
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded config from {env_path}")
    
    db = SessionLocal()
    try:
        print('--- Server-Side Healing Started ---')
        
        # 1. 清洗数据 (合并重复记录)
        print('Step 1: Cleaning teams...')
        cleaner = DataCleaner(db)
        cleaner.clean(dry_run=False)
        
        # 2. 刷新预测 (应用新逻辑)
        print('Step 2: Recalculating predictions...')
        engine = PredictionEngine(db_session=db)
        matches = db.query(Match).filter(Match.status != MatchStatus.FINISHED).all()
        
        for m in matches:
            # 删除旧预测
            db.query(Prediction).filter(Prediction.match_id == m.id).delete()
            try:
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=m.id, 
                        play_type=p["play_type"], 
                        probabilities=p["probabilities"],
                        confidence=res.confidence
                    ))
            except Exception as e:
                import traceback
                print(f'  Error on match {m.id}: {e}')
                traceback.print_exc()
                continue
                
        db.commit()
        print('--- Server-Side Healing Finished ---')
    finally:
        db.close()

if __name__ == "__main__":
    main()
