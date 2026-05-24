from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx
import logging

from models import get_db, Match, Team, Prediction
from logger import get_logger

router = APIRouter(prefix="/api/advisor", tags=["advisor"])
logger = get_logger("advisor")

# Deepstock API Configuration
ADVISOR_API_URL = "https://deepstock.zone.id/v1/chat/completions"
ADVISOR_API_KEY = "sk-F9fKBTHbEJE4AEY9aOy5IwwUdxOPrZ3NilvAnohOO1ODm1KT"
ADVISOR_MODEL = "google/gemma-2-2b-it"

class ChatMessage(BaseModel):
    role: str
    content: str

class AdvisorRequest(BaseModel):
    message: str
    match_id: Optional[int] = None
    history: List[ChatMessage] = []

class AdvisorResponse(BaseModel):
    reply: str

@router.post("/chat", response_model=AdvisorResponse)
async def advisor_chat(req: AdvisorRequest, db: Session = Depends(get_db)):
    """量化策略研判顾问对话"""
    context_parts = []

    if req.match_id:
        match = db.query(Match).filter(Match.id == req.match_id).first()
        if match:
            home = db.query(Team).filter(Team.id == match.home_team_id).first()
            away = db.query(Team).filter(Team.id == match.away_team_id).first()
            preds = db.query(Prediction).filter(Prediction.match_id == match.id).all()

            ctx = f"""[当前场次实时数据资产]
- 对阵: {home.name if home else '?'} vs {away.name if away else '?'}
- 时间: {match.kickoff_at}
- 赛事: {match.competition or '未知'}
- 状态: {match.status.value if hasattr(match.status, 'value') else match.status}
- 即时比分: {match.home_score if match.home_score is not None else '-'}:{match.away_score if match.away_score is not None else '-'}
"""
            for pred in preds:
                probs = pred.probabilities
                if pred.play_type == 'SPF':
                    ctx += f"- 量化模型校准SPF概率: 主胜 {probs.get('home','?')}% | 平局 {probs.get('draw','?')}% | 客胜 {probs.get('away','?')}% \n"
            
            context_parts.append(ctx)

    system_prompt = """你是一个冷酷、专业的【量化数据分析师】。你的目标是解析比赛背后的数学逻辑。

    【说话准则】：
    1. 严禁废话：不许说“综合考虑”、“欢迎咨询”、“仅供参考”等废话。
    2. 直击要点：开口就谈数据。
    3. 关键指标：
    - 如果 Elo 差值 > 100，指出实力悬殊。
    - 对比模型 SPF 概率与市场隐含概率（即赔率折算概率）。
    - 如果模型概率比市场高 3% 以上，称之为“正向 Edge”；反之则为“高估风险”。
    4. 裁判与伤病：如果有相关数据，直接指出其对进球数（λ）的影响。

    【回复模板示例】：
    “本场博弈点：量化模型给出主胜 55%，但市场赔率隐含概率仅 48%，存在 7% 的价值洼地。主裁吹罚尺度严，预期进球数降至 2.1，建议关注小比分波动。”

    请用中文回答。"""

    # 增强数据上下文的结构化程度
    if context_parts:
        full_user_message = f"【量化资产快照】\n" + "\n".join(context_parts) + f"\n\n【咨询需求】: {req.message}"
    else:
        full_user_message = req.message

    messages = [{"role": "user", "content": f"系统指令: {system_prompt}\n\n当前任务: {full_user_message}"}]

    logger.info(f"[advisor] Calling Gema-2 with hard-data prompt.")


    async with httpx.AsyncClient() as client:

        try:
            resp = await client.post(
                ADVISOR_API_URL,
                headers={
                    "Authorization": f"Bearer {ADVISOR_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ADVISOR_MODEL,
                    "messages": messages,
                    "temperature": 0.3, # 降低随机性，增加顾问的严谨度
                    "max_tokens": 1500,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return AdvisorResponse(reply=reply)
        except Exception as e:
            logger.error(f"[advisor] Deepstock API call failed: {e}")
            raise HTTPException(status_code=502, detail=f"顾问服务暂时忙碌: {str(e)}")
