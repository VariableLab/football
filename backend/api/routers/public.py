from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Any

from database.models import get_db, Team
from schemas import TeamListResponse, ArbitrageResponse
from hedge_engine import HedgeEngine

router = APIRouter(prefix="/api", tags=["Public"])

@router.get("/teams", response_model=TeamListResponse)
def list_teams(
    limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db),
):
    """List teams with pagination."""
    total = db.query(Team).count()
    items = db.query(Team).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "offset": offset, "limit": limit, "items": items}

@router.get("/arbitrage", response_model=ArbitrageResponse)
def get_arbitrage_opportunities(
    competition: str = "",
    db: Session = Depends(get_db),
):
    """Scan for cross-bookmaker arbitrage opportunities."""
    engine = HedgeEngine(db)
    opportunities = engine.scan_arbitrage(competition=competition)

    return {
        "count": len(opportunities),
        "opportunities": [
            {
                "match_id": o.match_id,
                "best_odds": {
                    "home": o.best_home_odds,
                    "draw": o.best_draw_odds,
                    "away": o.best_away_odds,
                },
                "bookmakers": {
                    "home": o.home_bookmaker,
                    "draw": o.draw_bookmaker,
                    "away": o.away_bookmaker,
                },
                "implied_total": round(o.implied_total, 4),
                "profit_pct": round(o.profit_pct, 4),
                "net_profit_pct": round(o.net_profit_pct, 4),
                "stakes": o.stakes,
                "is_genuine": o.is_genuine,
            }
            for o in opportunities
        ],
    }
