import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class StatsbombClient:
    """
    Client for fetching fine-grained event data from StatsBomb API.
    Provides mocked/free tier data by default for 2026 World Cup development.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.base_url = "https://data.statsbomb.com/api/v1"
        
    def get_match_events(self, match_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all events for a specific match.
        Includes passes, shots, tackles, etc.
        """
        # TODO: Implement real API call. Returning mock data for MVP.
        logger.info(f"Fetching StatsBomb events for match {match_id}...")
        return [
            {"type": "Shot", "player": "Lionel Messi", "xg": 0.35, "team": "Argentina"},
            {"type": "Pass", "player": "Enzo Fernandez", "outcome": "Complete", "team": "Argentina"},
            {"type": "Shot", "player": "Kylian Mbappe", "xg": 0.40, "team": "France"}
        ]
        
    def get_match_xg_summary(self, match_id: str) -> Dict[str, float]:
        """
        Calculate aggregated xG for both teams.
        """
        events = self.get_match_events(match_id)
        # Mock aggregation
        return {
            "home_xg": sum(e.get("xg", 0) for e in events if e.get("team") == "Argentina"),
            "away_xg": sum(e.get("xg", 0) for e in events if e.get("team") == "France")
        }

    def get_key_player_stats(self, match_id: str, player_name: str) -> Dict[str, Any]:
        """
        Get specific tactical stats for a key player.
        """
        events = self.get_match_events(match_id)
        player_events = [e for e in events if e.get("player") == player_name]
        return {
            "player": player_name,
            "total_xg": sum(e.get("xg", 0) for e in player_events if e.get("type") == "Shot"),
            "passes_completed": len([e for e in player_events if e.get("type") == "Pass" and e.get("outcome") == "Complete"])
        }
