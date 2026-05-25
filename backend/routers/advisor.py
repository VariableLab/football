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
    
    # 🕵️ 智能增强：如果用户在问“今日推荐”、“哪些值得买”等，自动注入全量 Top Picks
    keywords = ["推荐", "哪些", "值得", "今天", "买", "策略", "picks", "best", "today"]
    if any(k in req.message.lower() for k in keywords) and not req.match_id:
        try:
            top_data = get_top_picks(db)
            if top_data["top_picks"]:
                ctx_top = "[今日在售赛事量化 Top 5 优选]\n"
                for i, p in enumerate(top_data["top_picks"]):
                    ctx_top += f"{i+1}. {p['match']} | {p['selection']} (赔率{p['odds']}) | EV: {p['ev']:+.1%}\n"
                context_parts.append(ctx_top)
        except Exception as e:
            logger.warning(f"[advisor] Auto-fetch top picks failed: {e}")

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

@router.get("/top-picks")
def get_top_picks(db: Session = Depends(get_db)):
    """获取全量在售赛事的 Top 5 高价值研判"""
    from prediction_engine import PredictionEngine, build_context_from_match
    from strategy_pipeline import StrategyPipeline
    from models import Match, JingcaiIssueMatch
    
    engine = PredictionEngine()
    pipeline = StrategyPipeline(risk_tier='balanced', bankroll=100.0)

    # 1. 找到所有尚未开赛的竞彩/重要比赛
    matches = db.query(Match).join(JingcaiIssueMatch, Match.id == JingcaiIssueMatch.match_id).filter(Match.status != 'FINISHED').all()

    all_picks = []
    for m in matches:
        try:
            ctx = build_context_from_match(m)
            pred_res = engine.predict(ctx)
            
            # 构造生成策略所需的格式
            preds = [{'play_type': 'SPF', 'probabilities': pred_res.spf}]
            picks = pipeline.generate(
                predictions=preds,
                odds_home=m.odds_home or 2.0,
                odds_draw=m.odds_draw or 3.2,
                odds_away=m.odds_away or 3.5,
                competition=m.competition or '',
                match_id=m.id
            )
            
            for p in picks:
                if p.is_recommended:
                    all_picks.append({
                        'match': f"{m.home_team.name} vs {m.away_team.name}",
                        'league': m.competition,
                        'selection': p.selection_label,
                        'odds': p.odds,
                        'prob': p.model_prob_calibrated,
                        'ev': p.ev,
                        'kelly': p.stake_pct,
                        'rationale': p.rationale
                    })
        except Exception:
            continue

    # 2. 按 EV 排序并去重
    all_picks.sort(key=lambda x: x['ev'], reverse=True)
    return {"top_picks": all_picks[:5]}
