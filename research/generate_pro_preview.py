import os
import sys

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.models.poisson import PoissonPredictor
from footy.evaluation.visualizer import MatchCardGenerator
import pandas as pd

def main():
    print("💎 Generating Ultra-Intuitive PRO Preview for 2026...")
    
    # Simulation Case: Colombia vs Portugal
    match_pairing = "Colombia vs Portugal"
    
    # 1. Prediction logic
    model = PoissonPredictor()
    model.team_params = {
        "Colombia": {"att": 1.25, "def": 0.95},
        "Portugal": {"att": 1.45, "def": 0.85}
    }
    X_test = pd.DataFrame([{"HomeTeam": "Colombia", "AwayTeam": "Portugal"}])
    probs = model.predict_proba(X_test)[0]
    
    # 2. PRO Card Data
    card_data = {
        "match_info": {
            "pairing": match_pairing,
            "venue": "SoFi Stadium, Los Angeles"
        },
        "stats": {
            "avg_xg": {"home": 1.35, "away": 1.62},
            "h2h": "Last meeting: POR 2-1 COL"
        },
        "ai_analysis": {
            "content": "Portugal is expected to dominate through wing channels. Colombia will rely on central low block counters."
        },
        "prediction_ref": {
            "home_win": probs[0],
            "draw": probs[1],
            "away_win": probs[2]
        }
    }
    
    # 3. Generate PRO Card
    print(f"\n🎨 Rendering Ultra-Intuitive Card for {match_pairing}...")
    viz = MatchCardGenerator(output_dir="research/reports/cards/pro_series")
    path = viz.generate_pro_card(card_data)
    
    print("\n" + "="*50)
    print(f"✨ SUCCESS: PRO Content Generated.")
    print(f"📁 Image Path: {path}")
    print("="*50)

if __name__ == "__main__":
    main()
