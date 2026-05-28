"""
Agent Engine — ProQuant 智能体调度中心 (v1.0)

本模块实现了“智能体工作流”：
1. 意图与情境分析 (Situation Analysis)
2. 动态指令合成 (Dynamic Prompting)
3. 专家逻辑链生成 (Expert Reasoning)
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel
import logging

from core.agent_brain import PROJECT_MANIFESTO, get_agent_context_prompt
from core.agent_tools import AgentTools

logger = logging.getLogger("agent_engine")

class AgentContext(BaseModel):
    match_data: Optional[Dict[str, Any]] = None
    model_performance: Optional[Dict[str, Any]] = None
    logic_trace: Optional[Any] = None
    system_scan: Optional[Dict[str, Any]] = None
    user_profile: Optional[Dict[str, Any]] = None

class AgentEngine:
    def __init__(self, settings):
        self.settings = settings
        self.brain_context = get_agent_context_prompt()

    def perform_system_scan(self, db) -> Dict[str, Any]:
        """AI 主动调研全站现状"""
        tools = AgentTools()
        return {
            "anomalies": tools.scan_market_anomalies(db),
            "health": tools.get_system_health_brief(db),
            "sentiment": tools.get_market_sentiment(db)
        }

    def analyze_situation(self, ctx: AgentContext) -> str:
        if ctx.system_scan and not ctx.match_data:
            return "briefing_mode"
            
        data = ctx.match_data or {}
        edge = data.get("edge_h", 0)
        
        if edge > 0.05:
            return "value_hunter"
            
        if ctx.logic_trace:
            has_steam = any("异动" in s.name or "资金" in s.description for s in ctx.logic_trace.steps)
            if has_steam:
                return "market_analyst"
                
        if data.get("competition") and ("WC" in data["competition"] or "EPL" in data["competition"]):
            return "tactical_master"
            
        return "rational_quant"

    def build_dynamic_prompt(self, situation: str, ctx: AgentContext) -> str:
        personae = {
            "value_hunter": {
                "role": "第一性原理量化专家",
                "focus": "从底层概率模型出发，拆解市场定价的‘逻辑断裂点’。忽略情感，只看错价。",
                "style": "简洁、硬核、基于证据。像 Karpathy 解释神经网络一样解释赔率。"
            },
            "market_analyst": {
                "role": "博弈分析师",
                "focus": "寻找数据中的异常（Anomalies）。将资金异动视为‘信号’而非‘噪音’，剥离市场虚假繁荣。",
                "style": "批判性思维，避免直觉偏见。逻辑链条必须环环相扣。"
            },
            "tactical_master": {
                "role": "底层架构师 (Football Quant)",
                "focus": "分析球队实力的‘基本面’（Elo/xG）。解释系统是如何将复杂的赛场表现‘压缩’为三个概率数字的。",
                "style": "通透、清晰、技术导向。将复杂足球博弈简化为最本质的物理对抗。"
            },
            "rational_quant": {
                "role": "极简量化员",
                "focus": "忠实呈现 Logic Trace。解释为什么当前的预测是‘最小必要假设’下的最优解。",
                "style": "极度精简，拒绝任何修饰词。"
            },
            "briefing_mode": {
                "role": "首席量化分析 Agent (VidIQ 风格)",
                "focus": "主动向用户汇报全站的核心机会与系统健康度。不要等用户问，要主动‘揭密’。",
                "style": "极具洞察力、行动导向。像是在晨会上的首席策略师。"
            }
        }
        
        p = personae.get(situation, personae["rational_quant"])
        
        # 注入用户个性化信息 (千人千面)
        user_context_block = ""
        if ctx.user_profile:
            up = ctx.user_profile
            user_context_block = f"""
### [当前用户画像]
- 风险偏好: {up.get('risk_tolerance', 'balanced')}
- 基础本金: {up.get('base_bankroll', 1000.0)}
- 关注联赛: {up.get('preferred_leagues', [])}
- 个性化指令: {up.get('ai_behavior_prompt', '无')}

请确保你的建议符合该用户的风险偏好。如果是 Aggressive，可以推荐高 Edge 的冷门；如果是 Strict，只推荐高胜率稳健场次。
"""
        
        prompt = f"""### 核心认知准则 (VidIQ + Karpathy)
1. **先推理，后结论 (Chain of Thought)**: 展示从[数据观察]到[逻辑假设]再到[最终研判]的闭环。
2. **主动出击**: 如果发现了 Extreme Edge (极端错价)，必须在开头高能预警。
3. **零废话**: 严禁使用“值得注意的是”、“我们可以看到”等垃圾填充词。
4. **精确如代码**: 概率精确到 0.1%。
{user_context_block}
### 你的思维设定
- 你是 【{p['role']}】。
- 你的风格是: {p['style']}
- 核心焦点: {p['focus']}
"""
        return prompt


    def get_system_prompt(self, ctx: AgentContext) -> str:
        situation = self.analyze_situation(ctx)
        dynamic_instruction = self.build_dynamic_prompt(situation, ctx)
        
        return f"""你是一个深度集成在 WC Analytics 系统内部的智能 Agent。
        
{self.brain_context}

---
{dynamic_instruction}

【强制任务】:
请基于提供的 [数据资产] 对用户请求进行研判。你的回答应该是一篇逻辑自洽、具有独到见解的“专家专栏”。
⚠️ 一切以实际赛果为准。量化预测基于概率分布，无法做到 100% 准确，请理性参考。
"""

    def get_briefing_prompt(self, scan_data: Dict) -> str:
        return f"""你是 WC Analytics 的【首席量化代理】。
        
{self.brain_context}

### 当前任务: 生成一份 [今日量化早报]
你要根据下方 [全站扫描报告] 提取最关键的 3 个洞察。

### 扫描报告内容:
- 极端错价机会: {scan_data['anomalies']}
- 系统健康度: {scan_data['health']}
- 市场博弈情绪: {scan_data['sentiment']}

### 写作准则:
- 像 VidIQ 提醒博主视频趋势一样，直接指出最具吸引力的机会。
- 用逻辑串联起‘市场情绪’和‘系统健康’。
- 严禁模块化。末尾给出一个今日的‘金牌建议’。
"""
