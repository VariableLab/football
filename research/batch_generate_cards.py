import os
import sys

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.statsbomb import StatsBombLoader
from footy.evaluation.visualizer import MatchCardGenerator

def main():
    print("🚀 Starting Batch Content Generation (2022 World Cup Classics)...")
    
    # 2022 World Cup Match Selection
    match_list = [
        {"id": 3869685, "home": "Argentina", "away": "France", "desc": "The Greatest Final"},
        {"id": 3869254, "home": "Morocco", "away": "Portugal", "desc": "The African Dream"},
        {"id": 3869219, "home": "Croatia", "away": "Brazil", "desc": "The Underdog Strike"},
        {"id": 3869253, "home": "England", "away": "France", "desc": "European Giants Clash"},
        {"id": 3869220, "home": "Netherlands", "away": "Argentina", "desc": "The Dutch Drama"}
    ]
    
    loader = StatsBombLoader()
    viz = MatchCardGenerator(output_dir="research/reports/cards/batch_2022")
    
    generated_count = 0
    
    for match in match_list:
        print(f"\nProcessing: {match['home']} vs {match['away']}...")
        
        try:
            # 1. Fetch real xG from StatsBomb
            xg_data = loader.get_match_xg(match['id'])
            h_xg = xg_data.get(match['home'], 0)
            a_xg = xg_data.get(match['away'], 0)
            
            # 2. Synthesize data structure
            card_data = {
                "match_info": {
                    "pairing": f"{match['home']} vs {match['away']}",
                    "venue": "Qatar 2022 Stadium"
                },
                "stats": {
                    "avg_xg": {"home": h_xg, "away": a_xg},
                    "h2h": "World Cup Classic"
                },
                "ai_analysis": {
                    "headline": match['desc'],
                    "content": "Deep tactical analysis generated from event stream."
                },
                "prediction_ref": {
                    "home_win": 0.33, # Mocked for this demo
                    "draw": 0.34,
                    "away_win": 0.33
                }
            }
            
            # 3. Generate Card
            path = viz.generate_card(card_data)
            print(f"  ✅ Card saved: {path}")
            generated_count += 1
            
        except Exception as e:
            print(f"  ❌ Failed to process {match['id']}: {e}")

    print("\n" + "="*50)
    print(f"✨ BATCH COMPLETE: {generated_count} cards generated.")
    print(f"📁 Location: research/reports/cards/batch_2022/")
    print("="*50)

if __name__ == "__main__":
    main()
