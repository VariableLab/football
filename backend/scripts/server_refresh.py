
import sys
import os

# 💡 强力路径定位：确保在服务器脚本执行时能找到所有模块
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from database.models import SessionLocal, Match, Prediction, MatchStatus
from ingestion.data_cleaner import DataCleaner
from core.prediction_engine import PredictionEngine, build_context_from_match

def main():
    from dotenv import load_dotenv
    env_path = os.path.join(_backend_root, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    
    db = SessionLocal()
    try:
        print('--- Server-Side Healing Started ---')
        
        # 1. 清洗数据
        cleaner = DataCleaner(db)
        cleaner.clean(dry_run=False)
        
        # 2. 刷新预测
        engine = PredictionEngine(db_session=db)
        matches = db.query(Match).filter(Match.status != MatchStatus.FINISHED).all()
        
        success_count = 0
        total_count = len(matches)
        
        print(f'Processing {total_count} matches...')
        
        for m in matches:
            try:
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                
                # 只有生成成功才删除并重写 (防止全库变空)
                db.query(Prediction).filter(Prediction.match_id == m.id).delete()
                
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=m.id, 
                        play_type=p["play_type"], 
                        probabilities=p["probabilities"],
                        confidence=res.confidence
                    ))
                success_count += 1
            except Exception as e:
                print(f'  Error on match {m.id}: {e}')
                continue
                
        if success_count > 0:
            db.commit()
            print(f'--- Server-Side Healing Finished: {success_count}/{total_count} success ---')
        else:
            print('--- CRITICAL: 0 matches processed. Rolling back... ---')
            db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
