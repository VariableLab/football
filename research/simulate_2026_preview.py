import os
import sys

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.models.poisson import PoissonPredictor
from footy.evaluation.visualizer import MatchCardGenerator
import pandas as pd

def main():
    print("🌌 Running 2026 Parallel Universe Preview (Simulation)...")
    
    # 2026 Simulation Case: Colombia vs Portugal (Date: 2026-06-27)
    match_pairing = "Colombia vs Portugal"
    
    # 1. Use the expert Poisson model for prediction
    # Mock some training data to 'prime' the model (in reality, uses historical Centric data)
    model = PoissonPredictor()
    # Mocking team strengths
    model.team_params = {
        "Colombia": {"att": 1.25, "def": 0.95},
        "Portugal": {"att": 1.45, "def": 0.85}
    }
    
    X_test = pd.DataFrame([{"HomeTeam": "Colombia", "AwayTeam": "Portugal"}])
    probs = model.predict_proba(X_test)[0]
    
    # 2. Synthesize Card Data
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
            "content": "Portugal shows high threat in Zone 14. Colombia's low block will be tested by Leao's pace."
        },
        "prediction_ref": {
            "home_win": probs[0],
            "draw": probs[1],
            "away_win": probs[2]
        }
    }
    
    # 3. Generate Advanced Card with Heatmap
    print(f"\n🎨 Rendering Advanced Card with Tactical Heatmap for {match_pairing}...")
    viz = MatchCardGenerator(output_dir="research/reports/cards/2026_previews")
    path = viz.generate_advanced_card(card_data)
    
    print("\n" + "="*50)
    print(f"✨ SUCCESS: 2026 Simulation Content Generated.")
    print(f"📁 Image Path: {path}")
    print("📈 Model used: Poisson (Dixon-Coles) Expert implementation.")
    print("="*50)

if __name__ == "__main__":
    main()
