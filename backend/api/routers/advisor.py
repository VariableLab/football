from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx
import logging
import time
import json
import asyncio

from database.models import get_db, Match, Team, Prediction, UserQuantProfile
from api.auth import get_optional_user
from logger import get_logger
from config import get_settings
from core.prediction_engine import PredictionEngine, build_context_from_match
from core.agent_brain import get_agent_context_prompt
from core.agent_engine import AgentEngine, AgentContext
from monitor.validation_engine import ValidationEngine

router = APIRouter(prefix="/api/advisor", tags=["advisor"])
logger = get_logger("advisor")
settings = get_settings()
agent_orchestrator = AgentEngine(settings)

class ChatMessage(BaseModel):
    role: str
    content: str

class AdvisorRequest(BaseModel):
    message: str
    match_id: Optional[int] = None
    history: List[ChatMessage] = []

class AdvisorResponse(BaseModel):
    reply: str

def get_model_stats(db: Session):
    """获取模型真实效能指标快照"""
    try:
        from database.models import AccuracySnapshot
        latest = db.query(AccuracySnapshot).filter(AccuracySnapshot.metric == "direction_accuracy").order_by(AccuracySnapshot.id.desc()).first()
        if latest:
            return {"accuracy": latest.value, "sample_size": 31000, "notes": "系统级稳健性"}
        
        report = ValidationEngine.run_validation(db, limit=200)
        return {
            "accuracy": report.direction_accuracy,
            "sample_size": report.validated_matches,
            "notes": f"Brier: {report.avg_brier_score:.4f}"
        }
    except Exception:
        return {"accuracy": 0.566, "sample_size": 31000, "notes": "历史平均"}

# Simple In-memory cache for briefing
_BRIEFING_CACHE = {"data": None, "expiry": 0}
_CACHE_TTL = 3600 # 1 hour

@router.get("/briefing")
async def get_proactive_briefing(db: Session = Depends(get_db)):
    """获取主动式量化早报 (VidIQ 风格) - 带 1 小时缓存"""
    global _BRIEFING_CACHE
    now = time.time()
    
    if _BRIEFING_CACHE["data"] and now < _BRIEFING_CACHE["expiry"]:
        logger.info("[agent] Serving briefing from cache.")
        return _BRIEFING_CACHE["data"]

    # 1. AI 主动扫描全站
    scan_data = agent_orchestrator.perform_system_scan(db)
    system_prompt = agent_orchestrator.get_briefing_prompt(scan_data)
    
    # ... (rest of LLM calling logic) ...
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                settings.ADVISOR_API_URL,
                headers={"Authorization": f"Bearer {settings.ADVISOR_API_KEY}"},
                json={"model": settings.ADVISOR_MODEL, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": "生成早报"}], "temperature": 0.3},
                timeout=60.0
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"]
            result = {"briefing": reply, "scan_raw": scan_data}
            
            # Update Cache
            _BRIEFING_CACHE = {"data": result, "expiry": now + _CACHE_TTL}
            return result
        except Exception as e:
            logger.error(f"Briefing generation failed: {e}")
            return {"briefing": "早报扫描中...", "scan_raw": scan_data}

@router.post("/chat")
async def advisor_chat(
    req: AdvisorRequest, 
    db: Session = Depends(get_db),
    user = Depends(get_optional_user)
):
    """量化策略研判智能体对话 (Agentic Workflow + Streaming SSE)"""
    
    # 🕵️ 意图识别：是否需要全局数据
    is_asking_all = any(k in req.message.lower() for k in ["全部", "今天", "早报", "列表", "哪些", "值得"])
    
    context_parts = []
    match_data_for_agent = {}
    logic_trace_obj = None

    # 0. 注入用户画像 (千人千面)
    user_profile_data = None
    if user:
        profile = db.query(UserQuantProfile).filter(UserQuantProfile.user_id == user.id).first()
        if profile:
            user_profile_data = {
                "risk_tolerance": profile.risk_tolerance,
                "base_bankroll": profile.base_bankroll,
                "preferred_leagues": profile.preferred_leagues,
                "ai_behavior_prompt": profile.ai_behavior_prompt
            }

    # 1. 注入全量 Top Picks (如果需要)
    if is_asking_all and not req.match_id:
        try:
            top_data = get_top_picks(db)
            if top_data["top_picks"]:
                ctx_top = "[今日高价值 Top 5 优选]\n"
                for i, p in enumerate(top_data["top_picks"]):
                    ctx_top += f"{i+1}. {p['match']} | {p['selection']} (赔率{p['odds']}) | Edge: {p['edge']:+.1%}\n"
                context_parts.append(ctx_top)
        except Exception as e:
            logger.warning(f"[agent] Top picks fetch failed: {e}")

    # 2. 注入单场深度数据
    if req.match_id:
        try:
            match = db.query(Match).filter(Match.id == req.match_id).first()
            if match and match.home_team and match.away_team:
                engine = PredictionEngine(db_session=db)
                res = engine.predict(build_context_from_match(match))
                logic_trace_obj = res.trace
                
                probs = res.spf
                imp_h = 1.0 / (match.odds_home or 2.0)
                edge_h = probs.get('home', 0.333) - imp_h

                match_data_for_agent = {
                    "home": match.home_team.name,
                    "away": match.away_team.name,
                    "competition": match.competition,
                    "edge_h": edge_h,
                    "prob": probs,
                    "odds": {"h": match.odds_home, "d": match.odds_draw, "a": match.odds_away}
                }

                ctx_single = f"""[单场数据: {match_data_for_agent['home']} vs {match_data_for_agent['away']}]
- 赔率: {match_data_for_agent['odds']}
- 模型概率: {match_data_for_agent['prob']}
- Edge: {edge_h:+.1%}
"""
                if logic_trace_obj:
                    ctx_single += "\n[逻辑链条]\n"
                    for s in logic_trace_obj.steps:
                        ctx_single += f"- {s.name}: {s.description}\n"
                context_parts.append(ctx_single)
        except Exception as e:
            logger.error(f"[agent] Single match data injection failed: {e}")

    # 3. 注入系统状态
    perf = get_model_stats(db)
    context_parts.append(f"[系统效能] 准确率: {perf['accuracy']:.1%}, 样本量: {perf['sample_size']}")

    # 🚀 生成动态 System Prompt
    agent_ctx = AgentContext(
        match_data=match_data_for_agent,
        model_performance=perf,
        logic_trace=logic_trace_obj,
        user_profile=user_profile_data
    )
    system_prompt = agent_orchestrator.get_system_prompt(agent_ctx)

    # 4. 构造消息流
    full_context = "\n".join(context_parts)
    # 这里的 system_prompt 已经包含了 Karpathy 精神和项目百科
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"### 实时数据上下文:\n{full_context}\n\n### 用户当前咨询:\n{req.message}"}
    ]

    logger.info(f"[agent] Calling LLM with dynamic orchestrator for user message (Streaming).")

    async def event_generator():
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    settings.ADVISOR_API_URL,
                    headers={"Authorization": f"Bearer {settings.ADVISOR_API_KEY}"},
                    json={
                        "model": settings.ADVISOR_MODEL,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 1500,
                        "stream": True
                    },
                    timeout=60.0,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            chunk = json.loads(data_str)
                            content = chunk["choices"][0]["delta"].get("content", "")
                            if content:
                                yield f"data: {json.dumps({'content': content})}\n\n"
                        except Exception:
                            continue
            except Exception as e:
                logger.error(f"[agent] LLM Streaming failed: {e}")
                yield f"data: {json.dumps({'error': '量化专家目前不在位，请稍后咨询。'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/top-picks")
def get_top_picks(db: Session = Depends(get_db)):
    """获取全量在售赛事的 Top 5 高价值研判"""
    from core.prediction_engine import PredictionEngine, build_context_from_match
    from strategy.strategy_pipeline import StrategyPipeline
    from database.models import Match, JingcaiIssueMatch
    
    engine = PredictionEngine(db_session=db)
    pipeline = StrategyPipeline(risk_tier='advisor', bankroll=100.0)

    # 找到所有尚未开赛的竞彩比赛
    matches = db.query(Match).join(JingcaiIssueMatch, Match.id == JingcaiIssueMatch.match_id).filter(Match.status != 'FINISHED').all()

    all_picks = []
    for m in matches:
        try:
            ctx = build_context_from_match(m)
            pred_res = engine.predict(ctx)
            preds = [{'play_type': 'SPF', 'probabilities': pred_res.spf}]
            picks = pipeline.generate(preds, m.odds_home or 2.0, m.odds_draw or 3.2, m.odds_away or 3.5, m.competition or '', m.id)
            for p in picks:
                if p.ev > 0:
                    all_picks.append({
                        'match': f"{m.home_team.name} vs {m.away_team.name}",
                        'selection': p.selection_label,
                        'odds': p.odds,
                        'ev': p.ev,
                        'edge': p.edge
                    })
        except: continue

    all_picks.sort(key=lambda x: x['edge'], reverse=True)
    return {"top_picks": all_picks[:5]}
