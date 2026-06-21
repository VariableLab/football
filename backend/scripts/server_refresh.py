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
from sqlalchemy import or_

def main():
    db = SessionLocal()
    try:
        print('--- Server-Side Healing Started (v0.3.0-Ultra) ---')
        
        # 0. 强效修补核心球队数据 (含物理去重)
        print('Step 0: Deduplicating and fixing core teams...')
        CORE_DATA = {
            "巴西": {"code": "BRA", "name_en": "Brazil", "elo": 1950, "avg_xg": 2.10},
            "德国": {"code": "GER", "name_en": "Germany", "elo": 1820, "avg_xg": 1.95},
            "阿根廷": {"code": "ARG", "name_en": "Argentina", "elo": 2010, "avg_xg": 1.85},
            "法国": {"code": "FRA", "name_en": "France", "elo": 1980, "avg_xg": 2.20},
            "英格兰": {"code": "ENG", "name_en": "England", "elo": 1940, "avg_xg": 2.05},
            "日本": {"code": "JPN", "name_en": "Japan", "elo": 1720, "avg_xg": 1.65},
            "西班牙": {"code": "ESP", "name_en": "Spain", "elo": 1910, "avg_xg": 2.00},
            "乌拉圭": {"code": "URU", "name_en": "Uruguay", "elo": 1830, "avg_xg": 1.52},
            "沙特": {"code": "KSA", "name_en": "Saudi Arabia", "elo": 1622, "avg_xg": 1.10},
            "比利时": {"code": "BEL", "name_en": "Belgium", "elo": 1790, "avg_xg": 1.80},
            "埃及": {"code": "EGY", "name_en": "Egypt", "elo": 1610, "avg_xg": 1.25},
            "伊朗": {"code": "IRN", "name_en": "Iran", "elo": 1640, "avg_xg": 1.30},
            "新西兰": {"code": "NZL", "name_en": "New Zealand", "elo": 1420, "avg_xg": 1.05},
            "佛得角": {"code": "CPV", "name_en": "Cape Verde", "elo": 1510, "avg_xg": 1.15},
        }

        for name, data in CORE_DATA.items():
            # 找出所有可能的重复记录
            teams = db.query(Team).filter(or_(Team.name.like(f"%{name}%"), Team.code == data["code"])).all()
            if not teams: continue
            
            # 确定主记录 (ID 最小的那个)
            primary = sorted(teams, key=lambda x: x.id)[0]
            
            for t in teams:
                if t.id != primary.id:
                    # 迁移比赛关联
                    db.query(Match).filter(Match.home_team_id == t.id).update({Match.home_team_id: primary.id})
                    db.query(Match).filter(Match.away_team_id == t.id).update({Match.away_team_id: primary.id})
                    db.delete(t)
            
            # 更新主记录数据
            primary.name = name
            primary.code = data["code"]
            primary.name_en = data["name_en"]
            primary.elo = data["elo"]
            primary.avg_xg = data["avg_xg"]
        db.commit()

        # 1. 刷新预测
        print('Step 1: Recalculating predictions...')
        engine = PredictionEngine(db_session=db)
        matches = db.query(Match).filter(Match.status != MatchStatus.FINISHED).all()
        success = 0
        for m in matches:
            try:
                if not m.home_team or not m.away_team: 
                    print(f"  Match {m.id} skipped: missing team data")
                    continue
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                db.query(Prediction).filter(
                    Prediction.match_id == m.id,
                    Prediction.model_version.in_(["v2.0", "v3.0", "v3.0_shadow"])
                ).delete()
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=m.id, play_type=p["play_type"], 
                        probabilities=p["probabilities"],
                        model_version=p["model_version"], # 💡 使用 payload 返回的真实版本号
                        confidence=res.confidence
                    ))
                success += 1
            except Exception as e:
                if success < 5: # 只打印前 5 个错误以防刷屏
                    print(f"  Error on match {m.id} ({m.match_code}): {str(e)}")
                continue

        db.commit()
        print(f'--- Finished: {success}/{len(matches)} success ---')
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
