
import os
import sys
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

# Add backend to sys.path
_root = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.join(_root, "backend")
sys.path.append(_backend)
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_backend, d))

# Add research/src to sys.path for content engine
_research_src = os.path.join(_root, "research", "src")
sys.path.append(_research_src)

from database.models import SessionLocal, Match, Team, MatchStatus
from footy.content.ai_service import AIService
from utils.logger import get_logger

logger = get_logger("auto_poster")

def run_auto_poster_pipeline():
    """
    全自动海报生成流水线：
    1. 查找未来 48 小时内、尚未生成海报的焦点比赛（世界杯赛事）。
    2. 调用 Agnes AI 生成高保真海报。
    3. 自动更新数据库。
    """
    db = SessionLocal()
    ai = AIService()
    
    try:
        # 自动化判定逻辑：窗口期内的所有比赛，且 poster_url 为空
        now = datetime.now()
        start_window = now - timedelta(days=30)
        end_window = now + timedelta(days=30)
        
        matches = db.query(Match).filter(
            Match.kickoff_at >= start_window,
            Match.kickoff_at <= end_window,
            Match.poster_url == None
        ).all()
        
        if not matches:
            logger.info("未发现需要生成海报的焦点比赛。")
            return

        logger.info(f"发现 {len(matches)} 场比赛需要自动化生成海报。")

        for match in matches:
            try:
                home = match.home_team
                away = match.away_team
                
                # 检查英文名，如果缺失则回退到中文名（虽然生图英文更佳）
                home_name = home.name_en or home.name
                away_name = away.name_en or away.name
                
                logger.info(f"正在为 {match.match_code} ({home_name} vs {away_name}) 生成自动化海报...")
                
                poster_url = ai.generate_match_poster(home_name, away_name)
                
                if poster_url:
                    match.poster_url = poster_url
                    db.commit()
                    logger.info(f"✅ {match.match_code} 海报生成并存储成功: {poster_url}")
                else:
                    logger.error(f"❌ {match.match_code} 海报生成失败：API 未返回 URL")
                    
            except Exception as e:
                logger.error(f"发生错误处理比赛 {match.match_code}: {e}")
                db.rollback()
                
    finally:
        db.close()

if __name__ == "__main__":
    run_auto_poster_pipeline()
