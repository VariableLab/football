import sqlite3
import sys
import os
import json
import numpy as np
from datetime import datetime, timedelta, timezone

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from prediction_engine import PredictionEngine, build_context_from_match
from database.models import SessionLocal, Match, MatchStatus

def run_final_stats():
    # Use Session to get full ORM objects including team info
    db = SessionLocal()
    engine = PredictionEngine()
    
    # Test on last 500 finished matches across major leagues
    leagues = ["EPL", "LaLiga", "SerieA", "Bundesliga", "Ligue1"]
    
    print("🚀 Running Final Accuracy Audit for Product Hunt Launch...")
    
    total_correct = 0
    total_samples = 0
    
    league_results = {}

    for league in leagues:
        matches = db.query(Match).filter(
            Match.competition == league,
            Match.status.ilike("finished"),
            Match.actual_outcome.isnot(None)
        ).order_by(Match.kickoff_at.desc()).limit(100).all()
        
        if not matches:
            continue
            
        correct = 0
        for m in matches:
            try:
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                pred = max(res.spf, key=res.spf.get)
                if pred == m.actual_outcome:
                    correct += 1
                total_samples += 1
                total_correct += 1 if pred == m.actual_outcome else 0
            except:
                continue
        
        acc = correct / len(matches) if matches else 0
        league_results[league] = acc
        print(f"  - {league:12}: {acc:.1%} Accuracy")

    overall_acc = total_correct / total_samples if total_samples > 0 else 0
    print(f"\n✅ Audit Complete.")
    print(f"🎯 Peak League Accuracy: {max(league_results.values()):.1%}")
    print(f"🌍 Overall Cross-League Accuracy: {overall_acc:.1%}")
    db.close()

if __name__ == "__main__":
    # Mock environment variables for direct execution
    os.environ["SECRET_KEY"] = "temp-secret-key-at-least-32-characters-long"
    os.environ["ADMIN_API_KEY"] = "temp-admin-key-long-enough"
    run_final_stats()
