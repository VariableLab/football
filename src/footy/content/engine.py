import json
import logging
import os
from typing import Dict, Any, Optional

import google.generativeai as genai

from footy.data.statsbomb import StatsbombClient
from footy.data.football_data_org import FootballDataOrgClient
# Assumes core.prediction_engine and database are available when run inside backend/
from core.prediction_engine import PredictionEngine, build_context_from_match
from database.models import SessionLocal, Match

logger = logging.getLogger(__name__)

class ContentSynthesizer:
    """
    Synthesizes multi-source data (Statsbomb, Predictions, Match API) 
    and uses an LLM to generate structured Match Preview JSON cards.
    """
    def __init__(self, gemini_api_key: str = None):
        api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not found! Using dummy response.")
            self.use_dummy = True
        else:
            genai.configure(api_key=api_key)
            # Use Gemini 1.5 Pro for content synthesis
            self.model = genai.GenerativeModel('gemini-1.5-pro')
            self.use_dummy = False
            
        self.sb_client = StatsbombClient()
        self.fd_client = FootballDataOrgClient()
        self.db = SessionLocal()
        self.pred_engine = PredictionEngine(db_session=self.db)
        
    def generate_preview(self, match_code: str) -> Dict[str, Any]:
        """
        Generates a structured JSON preview for a given match code.
        """
        # 1. Fetch Match from DB
        match = self.db.query(Match).filter(Match.match_code == match_code).first()
        if not match:
            raise ValueError(f"Match {match_code} not found in DB.")
            
        # 2. Extract AI Prediction Data (v4.0)
        ctx = build_context_from_match(match)
        res = self.pred_engine.predict(ctx)
        spf = res.spf
        
        # Determine trend
        h_prob = spf.get("home", 0)
        d_prob = spf.get("draw", 0)
        a_prob = spf.get("away", 0)
        trend = "主队优势 🔼" if h_prob > max(d_prob, a_prob) else ("客队优势 🔽" if a_prob > max(d_prob, h_prob) else "胶着/易平 ⚖️")
        
        score_preds = res.score
        top_scores = []
        if score_preds:
            sorted_scores = sorted(score_preds.items(), key=lambda x: x[1], reverse=True)[:3]
            top_scores = [k for k, _ in sorted_scores]
            
        # 3. Fetch Event Data (StatsBomb Mock)
        # Using home team's top player as mock focus
        key_player_stats = self.sb_client.get_key_player_stats(str(match.id), "Lionel Messi") 
        xg_summary = self.sb_client.get_match_xg_summary(str(match.id))

        # 4. Construct Prompt
        prompt = f"""
        你是一位专业的足球解说员与数据分析师。请基于以下数据，生成一场比赛的【前瞻战术卡片】，并严格输出为JSON格式。
        
        [基本信息]
        赛事: {match.competition or "World Cup 2026"}
        对阵: {match.home_team.name} vs {match.away_team.name}
        
        [AI v4.0 模型预测]
        主胜: {h_prob:.1%}
        平局: {d_prob:.1%}
        客胜: {a_prob:.1%}
        比分推荐: {', '.join(top_scores)}
        预测倾向: {trend}
        
        [战术事件数据 (xG)]
        预估主队 xG: {xg_summary.get('home_xg', 0):.2f}
        预估客队 xG: {xg_summary.get('away_xg', 0):.2f}
        关键球员焦点: {key_player_stats.get('player', 'N/A')} (预期 xG: {key_player_stats.get('total_xg', 0):.2f})
        
        要求输出完全合法的 JSON（不要包含 ```json 标签等 Markdown），格式如下：
        {{
          "match_id": "{match_code}",
          "match_info": {{
            "competition": "{match.competition}",
            "home_team": "{match.home_team.name}",
            "away_team": "{match.away_team.name}",
            "kickoff": "{match.kickoff_at}"
          }},
          "ai_predictions": {{
            "home_win_prob": {h_prob:.2f},
            "draw_prob": {d_prob:.2f},
            "away_win_prob": {a_prob:.2f},
            "recommended_score": {json.dumps(top_scores)},
            "ai_trend": "{trend}"
          }},
          "content_cards": {{
            "preview": {{
              "title": "<这里写一句有张力的标题，比如：卫冕冠军的防线大考！>",
              "tactical_focus": "<这里写战术分析段落，结合胜率和xG，约50字>",
              "key_player": "<关键球员分析，结合给出的数据>"
            }},
            "xg_analysis": {{
              "home_xg": {xg_summary.get('home_xg', 0):.2f},
              "away_xg": {xg_summary.get('away_xg', 0):.2f},
              "analysis": "<结合双方预期xG，写一段前瞻性的战术对比分析>"
            }}
          }}
        }}
        """

        if self.use_dummy:
            # Fallback for local testing without API Key
            logger.info("Generating DUMMY JSON payload...")
            return {
              "match_id": match_code,
              "match_info": {
                "competition": match.competition,
                "home_team": match.home_team.name,
                "away_team": match.away_team.name,
                "kickoff": str(match.kickoff_at)
              },
              "ai_predictions": {
                "home_win_prob": h_prob,
                "draw_prob": d_prob,
                "away_win_prob": a_prob,
                "recommended_score": top_scores,
                "ai_trend": trend
              },
              "content_cards": {
                "preview": {
                  "title": f"【DUMMY】{match.home_team.name} 迎战 {match.away_team.name}，巅峰对决！",
                  "tactical_focus": "本场比赛重点在于中场拦截，AI 判定主队有极大优势。",
                  "key_player": f"{key_player_stats.get('player')} 状态极佳"
                },
                "xg_analysis": {
                  "home_xg": xg_summary.get('home_xg', 0),
                  "away_xg": xg_summary.get('away_xg', 0),
                  "analysis": "主队预期进球略高，看好主场拿下。"
                }
              }
            }

        logger.info("Calling Gemini API to synthesize content...")
        response = self.model.generate_content(prompt)
        text = response.text.strip()
        # Clean up markdown
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
            
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM output: {text}")
            raise e

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    code = sys.argv[1] if len(sys.argv) > 1 else "JC-20260620-美国-澳大利"
    synth = ContentSynthesizer()
    res = synth.generate_preview(code)
    print(json.dumps(res, ensure_ascii=False, indent=2))
