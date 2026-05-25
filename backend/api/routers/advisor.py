from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx
import logging

from database.models import get_db, Match, Team, Prediction
from utils.logger import get_logger

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
    # 🕵️ 核心约束：非数据相关问题直接拒答（前置过滤）
    safe_keywords = ["球", "胜", "平", "负", "赔", "edge", "ev", "roi", "模型", "推荐", "哪些", "值得", "分析", "场", "利", "曼", "阿", "皇", "巴", "赛"]
    is_data_query = any(k in req.message.lower() for k in safe_keywords) or req.match_id
    
    if not is_data_query and len(req.message) < 50: # 允许长难句进入 LLM 判断，短句直接过滤
        return AdvisorResponse(reply="抱歉，我目前仅被授权访问 ProQuant 足球量化终端的实时数据流，无法处理该范围外的咨询。您可以询问具体场次或点击上方按钮获取今日优选。")

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
                    # 计算市场隐含概率和 Edge
                    imp_h = 1.0 / (match.odds_home or 2.0)
                    imp_d = 1.0 / (match.odds_draw or 3.2)
                    imp_a = 1.0 / (match.odds_away or 3.5)
                    total_imp = imp_h + imp_d + imp_a
                    mkt_h, mkt_d, mkt_a = imp_h/total_imp, imp_d/total_imp, imp_a/total_imp
                    
                    edge_h = probs.get('home', 33.3)/100.0 - mkt_h
                    edge_d = probs.get('draw', 33.3)/100.0 - mkt_d
                    edge_a = probs.get('away', 33.3)/100.0 - mkt_a
                    
                    ctx += f"- 量化模型校准SPF概率: 主胜 {probs.get('home','?')}% | 平局 {probs.get('draw','?')}% | 客胜 {probs.get('away','?')}% \n"
                    ctx += f"- 市场错价识别 (Edge): 主胜 {edge_h:+.1%} | 平局 {edge_d:+.1%} | 客胜 {edge_a:+.1%} \n"
                    ctx += f"- 期望价值 (EV): 主胜 {((probs.get('home',0)/100.0)*(match.odds_home or 0)-1):+.1%} | 客胜 {((probs.get('away',0)/100.0)*(match.odds_away or 0)-1):+.1%} \n"
            
            context_parts.append(ctx)

    system_prompt = """你是一个严谨、甚至有些傲慢的【ProQuant 首席量化顾问】。你唯一的知识来源是 [当前场次实时数据资产] 和 [今日在售赛事量化 Top 5 优选]。

    【核心指令】：
    1. 绝对忠诚于数据：如果用户询问任何与本项目数据（足球、赔率、模型、神经网络）无关的问题，你必须礼貌但坚定地拒绝回答。
    2. 解释模型逻辑：你要向用户解释，当前的建议是基于 48 维特征向量和利润导向（ROI-driven）神经网络残差修正后的结果。
    3. 说人话：虽然专业，但要用人类博弈者能听懂的语言。不要复读原始概率，要转化为“博弈价值”或“错价空间”。
    4. 禁止 AI 废话：严禁说“总之”、“欢迎再次咨询”、“作为一个AI”等。直接切入数据，回答完毕即停止。

    【逻辑优先级】：
    - 第一优先级：利润导向 (ROI)。如果 EV (期望价值) > 5%，这就是你的核心推荐逻辑。
    - 第二优先级：神经网络残差修正。指出模型检测到了市场赔率的系统性偏差。
    - 第三优先级：联赛垂直模型。如果是五大联赛，强调这是针对该联赛风格的专属“大脑”算出的结果。

    【拒答逻辑】：
    如果用户问“你好”、“你是谁”、“写个代码”或关于篮球等，统一回答：“抱歉，我目前仅被授权访问 ProQuant 足球量化终端的实时数据流，无法处理该范围外的咨询。”

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
    from core.prediction_engine import PredictionEngine, build_context_from_match
    from strategy.strategy_pipeline import StrategyPipeline
    from database.models import Match, JingcaiIssueMatch
    
    engine = PredictionEngine()
    pipeline = StrategyPipeline(risk_tier='advisor', bankroll=100.0)

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
                # 只要 EV > 0 (即理论盈利) 就加入备选，由 LLM 决定如何推荐
                if p.ev > 0:
                    all_picks.append({
                        'match': f"{m.home_team.name} vs {m.away_team.name}",
                        'league': m.competition,
                        'selection': p.selection_label,
                        'odds': p.odds,
                        'prob': p.model_prob_calibrated,
                        'ev': p.ev,
                        'edge': p.edge,
                        'kelly': p.stake_pct,
                        'rationale': p.rationale
                    })
        except Exception:
            continue

    # 2. 按 Edge (错价程度) 排序并去重
    all_picks.sort(key=lambda x: x['edge'], reverse=True)
    return {"top_picks": all_picks[:5]}
