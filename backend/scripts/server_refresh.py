
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
        
        # 0. 强效修补核心球队数据
        print('Step 0: Fixing core team data (Expanded set)...')
        from backend.database.models import Team
        CORE_DATA = {
            "巴西": {"code": "BRA", "name_en": "Brazil", "elo": 1950, "avg_xg": 2.10},
            "德国": {"code": "GER", "name_en": "Germany", "elo": 1820, "avg_xg": 1.95},
            "阿根廷": {"code": "ARG", "name_en": "Argentina", "elo": 2010, "avg_xg": 1.85},
            "法国": {"code": "FRA", "name_en": "France", "elo": 1980, "avg_xg": 2.20},
            "英格兰": {"code": "ENG", "name_en": "England", "elo": 1940, "avg_xg": 2.05},
            "日本": {"code": "JPN", "name_en": "Japan", "elo": 1720, "avg_xg": 1.65},
            "埃及": {"code": "EGY", "name_en": "Egypt", "elo": 1620, "avg_xg": 1.25},
            "西班牙": {"code": "ESP", "name_en": "Spain", "elo": 1910, "avg_xg": 2.00},
            "荷兰": {"code": "NED", "name_en": "Netherlands", "elo": 1880, "avg_xg": 1.90},
            "葡萄牙": {"code": "POR", "name_en": "Portugal", "elo": 1890, "avg_xg": 1.95},
            "意大利": {"code": "ITA", "name_en": "Italy", "elo": 1850, "avg_xg": 1.70},
            "比利时": {"code": "BEL", "name_en": "Belgium", "elo": 1840, "avg_xg": 1.80},
            "克罗地亚": {"code": "CRO", "name_en": "Croatia", "elo": 1780, "avg_xg": 1.55},
            "乌拉圭": {"code": "URU", "name_en": "Uruguay", "elo": 1790, "avg_xg": 1.60},
            "墨西哥": {"code": "MEX", "name_en": "Mexico", "elo": 1750, "avg_xg": 1.65},
            "美国": {"code": "USA", "name_en": "United States", "elo": 1760, "avg_xg": 1.70},
            "摩洛哥": {"code": "MAR", "name_en": "Morocco", "elo": 1740, "avg_xg": 1.45},
            "塞内加尔": {"code": "SEN", "name_en": "Senegal", "elo": 1710, "avg_xg": 1.40},
            "韩国": {"code": "KOR", "name_en": "South Korea", "elo": 1690, "avg_xg": 1.50},
            "澳大利亚": {"code": "AUS", "name_en": "Australia", "elo": 1680, "avg_xg": 1.40},
            "瑞士": {"code": "SUI", "name_en": "Switzerland", "elo": 1730, "avg_xg": 1.50},
            "丹麦": {"code": "DEN", "name_en": "Denmark", "elo": 1720, "avg_xg": 1.55},
            "塞尔维亚": {"code": "SRB", "name_en": "Serbia", "elo": 1670, "avg_xg": 1.60},
            "加拿大": {"code": "CAN", "name_en": "Canada", "elo": 1650, "avg_xg": 1.55},
            "秘鲁": {"code": "PER", "name_en": "Peru", "elo": 1640, "avg_xg": 1.30},
            "尼日利亚": {"code": "NGA", "name_en": "Nigeria", "elo": 1660, "avg_xg": 1.45},
            "突尼斯": {"code": "TUN", "name_en": "Tunisia", "elo": 1610, "avg_xg": 1.20},
            "哥伦比亚": {"code": "COL", "name_en": "Colombia", "elo": 1770, "avg_xg": 1.65},
            "智利": {"code": "CHI", "name_en": "Chile", "elo": 1690, "avg_xg": 1.40},
            "厄瓜多尔": {"code": "ECU", "name_en": "Ecuador", "elo": 1700, "avg_xg": 1.50},
        }
        
        updated_teams = []
        for name, data in CORE_DATA.items():
            # 💡 强力匹配：清除首尾空格
            teams = db.query(Team).filter(Team.name.like(f"%{name}%")).all()
            for t in teams:
                t.code = data["code"]
                t.name_en = data["name_en"]
                t.elo = data["elo"]
                t.avg_xg = data["avg_xg"]
                updated_teams.append(t.name)
        
        db.commit()
        print(f"  Updated {len(updated_teams)} core team records: {set(updated_teams)}")

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
