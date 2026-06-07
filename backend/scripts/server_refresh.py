import sys
import os

# 💡 强力路径定位
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

# 强制加载环境
from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_root, ".env"))

from database.models import SessionLocal, Match, Prediction, MatchStatus, Team
from ingestion.data_cleaner import DataCleaner
from core.prediction_engine import PredictionEngine, build_context_from_match

def main():
    db = SessionLocal()
    try:
        print('--- Server-Side Healing Started (v0.3.0) ---')
        
        # 0. 强效修补核心球队数据
        print('Step 0: Fixing core team data...')
        CORE_DATA = {
            "巴西": {"code": "BRA", "name_en": "Brazil", "elo": 1950, "avg_xg": 2.10},
            "德国": {"code": "GER", "name_en": "Germany", "elo": 1820, "avg_xg": 1.95},
            "阿根廷": {"code": "ARG", "name_en": "Argentina", "elo": 2010, "avg_xg": 1.85},
            "法国": {"code": "FRA", "name_en": "France", "elo": 1980, "avg_xg": 2.20},
            "英格兰": {"code": "ENG", "name_en": "England", "elo": 1940, "avg_xg": 2.05},
        }
        for name, data in CORE_DATA.items():
            teams = db.query(Team).filter(Team.name.like(f"%{name}%")).all()
            for t in teams:
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
        
        success = 0
        for m in matches:
            try:
                # 核心修正：跳过没有球队关联的脏数据
                if not m.home_team or not m.away_team: continue
                
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                db.query(Prediction).filter(Prediction.match_id == m.id).delete()
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=m.id, play_type=p["play_type"], 
                        probabilities=p["probabilities"],
                        model_version="v2.0-quant-fixed",
                        confidence=res.confidence
                    ))
                success += 1
            except: continue
                
        db.commit()
        print(f'--- Finished: {success}/{len(matches)} success ---')
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
