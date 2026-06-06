import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta, timezone

# 确保路径正确
PROJECT_ROOT = os.getcwd()
sys.path.append(os.path.join(PROJECT_ROOT, "research", "src"))

from footy.models.poisson import PoissonPredictor
from footy.evaluation.visualizer import MatchCardGenerator
from footy.content.ai_service import AIService

def main():
    print("🔥 启动 2026 世界杯 AI 灵魂解说集成任务...")
    
    # 1. 模拟 2026 对决数据
    match_pairing = "Portugal vs Colombia"
    model = PoissonPredictor()
    model.team_params = {
        "Portugal": {"att": 1.45, "def": 0.85},
        "Colombia": {"att": 1.25, "def": 0.95}
    }
    X_test = pd.DataFrame([{"HomeTeam": "Portugal", "AwayTeam": "Colombia"}])
    probs = model.predict_proba(X_test)[0]
    
    match_data = {
        "match_info": {"pairing": match_pairing, "venue": "SoFi Stadium"},
        "stats": {"avg_xg": {"home": 1.62, "away": 1.35}},
        "prediction_ref": {"home_win": probs[0], "draw": probs[1], "away_win": probs[2]}
    }

    # 2. 调用 GPT-OSS-120B 生成真实解说
    print("🤖 正在从 GPT-OSS-120B 获取专家点评...")
    ai = AIService()
    commentary = ai.analyze_match(match_data, 'zh')
    print(f"   [AI 解说]: {commentary}")
    
    # 3. 注入解说并生成 PRO 卡片
    match_data['ai_analysis'] = {"content": commentary}
    
    print("\n🎨 正在渲染带 AI 灵魂解算的 PRO 卡片...")
    viz = MatchCardGenerator(output_dir="research/reports/cards/ai_integrated")
    path = viz.generate_pro_card(match_data)
    
    print("\n" + "="*50)
    print(f"✨ 成功! 2026 AI 战术前瞻已生成。")
    print(f"📁 路径: {path}")
    print("="*50)

if __name__ == "__main__":
    main()
