
import os
import sys
import asyncio
import httpx
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# 设置路径以引入 backend 和 research/src
_root = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.join(_root, "backend")
sys.path.append(_backend)
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_backend, d))

_research_src = os.path.join(_root, "research", "src")
sys.path.append(_research_src)

from database.models import SessionLocal, Match, MatchStatus, Prediction, PlayType
from database.config import get_settings
from footy.content.engine import WorldCupContentEngine
from utils.logger import get_logger

logger = get_logger("broadcast_engine")
settings = get_settings()

class BroadcastEngine:
    """
    全自动赛事播报引擎：
    整合 AI 海报、AI 战术评论和模型预测概率，自动推送到 Telegram。
    """
    def __init__(self):
        self.tg_token = settings.TELEGRAM_BOT_TOKEN
        self.tg_chat_id = settings.TELEGRAM_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.tg_token}"
        
    async def send_broadcast(self, poster_url: str, caption: str):
        """发送带图文的消息"""
        async with httpx.AsyncClient() as client:
            try:
                # 优先发送照片，文字作为说明文字 (caption)
                res = await client.post(
                    f"{self.api_url}/sendPhoto",
                    json={
                        "chat_id": self.tg_chat_id,
                        "photo": poster_url,
                        "caption": caption,
                        "parse_mode": "Markdown"
                    },
                    timeout=30
                )
                if res.status_code == 200:
                    return True
                else:
                    logger.error(f"Telegram 发送失败: {res.text}")
                    return False
            except Exception as e:
                logger.error(f"广播发送异常: {e}")
                return False

    def _format_caption(self, preview_data: dict) -> str:
        """格式化播报文案"""
        m = preview_data['match_info']
        q = preview_data['quant']['probabilities']
        content = preview_data['content']
        
        # 组装 Markdown 文案
        caption = (
            f"🏆 **{m['pairing']}**\n"
            f"📅 开赛时间: `{m['kickoff']}`\n"
            f"🏟️ 场馆: {m['venue']}\n\n"
            f"🤖 **模型研判**\n"
            f"🏠 主胜: `{q['home']:.1%}`\n"
            f"🤝 平局: `{q['draw']:.1%}`\n"
            f"🚩 客胜: `{q['away']:.1%}`\n\n"
            f"📝 **战术前瞻**\n"
            f"{content['insight']}\n\n"
            f"🔗 [查看完整量化报告](https://football.nett.to/match/{m['id']})"
        )
        return caption

    async def run_pipeline(self):
        """运行播报流水线"""
        db = SessionLocal()
        content_engine = WorldCupContentEngine(db)
        
        try:
            # 1. 查找未来 12 小时内，已有海报但未播报的焦点比赛
            now = datetime.now()
            window = now + timedelta(hours=12)
            
            matches = db.query(Match).filter(
                Match.kickoff_at >= now,
                Match.kickoff_at <= window,
                Match.poster_url != None,
                Match.is_broadcasted == False
            ).all()
            
            if not matches:
                logger.info("未发现需要播报的即时焦点赛事。")
                return

            logger.info(f"发现 {len(matches)} 场焦点赛事准备播报。")

            for match in matches:
                try:
                    # 2. 生成内容预览
                    preview = content_engine.generate_match_preview(match.id)
                    if not preview:
                        continue
                    
                    caption = self._format_caption(preview)
                    
                    # 3. 发送广播
                    logger.info(f"正在播报: {match.match_code}...")
                    success = await self.send_broadcast(match.poster_url, caption)
                    
                    if success:
                        match.is_broadcasted = True
                        db.commit()
                        logger.info(f"✅ {match.match_code} 自动播报成功。")
                    
                except Exception as e:
                    logger.error(f"处理比赛 {match.match_code} 播报时出错: {e}")
                    db.rollback()
                    
        finally:
            db.close()

if __name__ == "__main__":
    engine = BroadcastEngine()
    asyncio.run(engine.run_pipeline())
