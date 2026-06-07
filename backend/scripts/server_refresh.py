
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
        
        # 0. 强制修补核心球队数据
        print('Step 0: Fixing core team data...')
        from backend.database.models import Team
        CORE_DATA = {
            "巴西": {"code": "BRA", "name_en": "Brazil", "elo": 1950, "avg_xg": 2.10},
            "德国": {"code": "GER", "name_en": "Germany", "elo": 1820, "avg_xg": 1.95},
            "阿根廷": {"code": "ARG", "name_en": "Argentina", "elo": 2010, "avg_xg": 1.85},
            "法国": {"code": "FRA", "name_en": "France", "elo": 1980, "avg_xg": 2.20},
            "英格兰": {"code": "ENG", "name_en": "England", "elo": 1940, "avg_xg": 2.05},
            "日本": {"code": "JPN", "name_en": "Japan", "elo": 1720, "avg_xg": 1.65},
            "埃及": {"code": "EGY", "name_en": "Egypt", "elo": 1620, "avg_xg": 1.25},
        }
        for name, data in CORE_DATA.items():
            t = db.query(Team).filter(Team.name == name).first()
            if t:
                t.code = data["code"]; t.name_en = data["name_en"]
                t.elo = data["elo"]; t.avg_xg = data["avg_xg"]
        db.commit()

        # 1. 清洗数据
        print('Step 1: Cleaning teams...')
        cleaner = DataCleaner(db)
        cleaner.clean(dry_run=False)
        
        # 2. 刷新预测
        print('Step 2: Recalculating predictions...')
        engine = PredictionEngine(db_session=db)
        matches = db.query(Match).filter(Match.status != MatchStatus.FINISHED).all()
        
        success_count = 0
        total_count = len(matches)
        
        print(f'Processing {total_count} matches...')
        
        for m in matches:
            try:
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                
                # 只有生成成功才删除并重写
                db.query(Prediction).filter(Prediction.match_id == m.id).delete()
                
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=m.id, 
                        play_type=p["play_type"], 
                        probabilities=p["probabilities"],
                        model_version=res.model_version,
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
