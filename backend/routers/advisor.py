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

    system_prompt = """你是一个专业的【量化研判顾问】。你的职责是基于多维数学模型输出和市场概率数据，为用户提供客观、严谨、逻辑化的深度数据解读。

【行为规范】：
1. 身份：你被称为“量化研判顾问”。
2. 风格：金融分析级风格，简洁严谨。避免情绪化词汇。
3. 逻辑：利用模型概率进行相关性分析。如果模型与市场共识存在偏差，请客观指出这种数学上的非对称性（Edge）。
4. 合规：严禁预测未来或给出具体的投注诱导。始终强调数据是基于历史统计推演的概率，具有不确定性。
5. 宗旨：通过量化视角辅助用户理解比赛数据背后的逻辑。

请用中文回答。你的所有输出仅限学术研究与数据分析参考。"""


    if context_parts:
        system_prompt += "\n\n当前输入的数据上下文:\n" + "\n".join(context_parts)

    messages = [{"role": "system", "content": system_prompt}]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

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
