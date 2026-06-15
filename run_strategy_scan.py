
import os
import sys
import json
from sqlalchemy.orm import Session

# Add backend subdirectories to path
_root = os.path.join(os.getcwd(), 'backend')
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

from database.models import SessionLocal, Match, Team, Prediction, PlayType
from strategy_config import load_params
from edge_calculator import EdgeCalculator

def run_ev_scan():
    db = SessionLocal()
    ec = EdgeCalculator()
    params = load_params()
    
    print(f"\n{'='*60}")
    print(f"  🔍 2026 世界杯外置策略 EV 深度扫描")
    print(f"{'='*60}")
    
    # Get WC2026 matches
    matches = db.query(Match).filter(Match.competition == "WC2026").all()
    
    results = []
    
    for m in matches:
        # Get SPF prediction
        pred = db.query(Prediction).filter(
            Prediction.match_id == m.id,
            Prediction.play_type == PlayType.SPF
        ).first()
        
        if not pred or not m.odds_home:
            continue
            
        probs = pred.probabilities
        # Probabilities are stored as strings in some DBs, or JSON in others. 
        # Prediction model uses JSON column, so it should be a dict.
        
        # Market odds
        odds = {
            "home": m.odds_home,
            "draw": m.odds_draw,
            "away": m.odds_away
        }
        
        # Calculate EV for each outcome
        evs = {}
        edges = {}
        for outcome in ["home", "draw", "away"]:
            prob = probs.get(outcome, 0)
            market_odd = odds.get(outcome, 1.0)
            # EV = Prob * Odds
            ev = prob * market_odd
            # Edge = Prob - (1/Odds)
            market_prob = 1.0 / market_odd
            edge = prob - market_prob
            
            evs[outcome] = ev
            edges[outcome] = edge
            
        # Find best EV
        best_outcome = max(evs, key=evs.get)
        
        results.append({
            "match": m,
            "best_outcome": best_outcome,
            "ev": evs[best_outcome],
            "edge": edges[best_outcome],
            "probs": probs,
            "odds": odds
        })
        
    # Sort by EV descending
    results.sort(key=lambda x: x["ev"], reverse=True)
    
    for r in results:
        m = r["match"]
        home = db.get(Team, m.home_team_id)
        away = db.get(Team, m.away_team_id)
        
        status_icon = "🔥 HIGH VALUE" if r["ev"] > 1.10 else ("⚖️ MEDIUM" if r["ev"] > 1.02 else "⚠️ SKIP")
        
        outcome_label = {"home": "主胜", "draw": "平局", "away": "客胜"}[r["best_outcome"]]
        
        print(f"[{m.match_code}] {home.name} vs {away.name}")
        print(f"  建议方向: {outcome_label} ({r['best_outcome']})")
        print(f"  模型概率: {r['probs'][r['best_outcome']]:.1%}")
        print(f"  市场赔率: {r['odds'][r['best_outcome']]:.2f}")
        print(f"  期望价值 (EV): {r['ev']:.3f} | 优势 (Edge): {r['edge']:+.1%}")
        print(f"  策略状态: {status_icon}")
        print("-" * 30)

    db.close()

if __name__ == "__main__":
    run_ev_scan()
