
import os
import sys
from sqlalchemy.orm import Session

# Add backend subdirectories to path
_root = os.path.join(os.getcwd(), 'backend')
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

from database.models import SessionLocal, Match, MatchStatus, Prediction, PlayType
from core.prediction_engine import PredictionEngine, build_context_from_match
from utils.logger import get_logger

logger = get_logger("group_predictions")

def run_predictions():
    db = SessionLocal()
    try:
        # Filter for all group stage matches (including upcoming and scheduled)
        matches = db.query(Match).filter(Match.stage == "group").all()
        print(f"Found {len(matches)} group stage matches.")
        
        engine = PredictionEngine(db_session=db)
        count = 0
        
        for match in matches:
            # Clear old predictions for this match
            db.query(Prediction).filter(Prediction.match_id == match.id).delete()
            
            try:
                ctx = build_context_from_match(match)
                if ctx is None:
                    continue
                
                result = engine.predict(ctx)
                
                # Save predictions
                for payload in result.to_db_payload():
                    pred = Prediction(
                        match_id=match.id,
                        play_type=payload["play_type"],
                        probabilities=payload["probabilities"],
                        model_version="v3.0-group-batch",
                        confidence=match.confidence
                    )
                    db.add(pred)
                
                count += 1
                if count % 5 == 0:
                    print(f"Processed {count}/{len(matches)}: {match.match_code}")
                    db.commit() # Periodic commit
                    
            except Exception as e:
                print(f"Error predicting {match.match_code}: {e}")
        
        db.commit()
        print(f"Successfully generated predictions for {count} matches.")
    finally:
        db.close()

if __name__ == "__main__":
    run_predictions()
