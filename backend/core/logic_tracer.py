from typing import Dict, List, Any
from pydantic import BaseModel

class TraceStep(BaseModel):
    name: str
    description: str
    impact_home: float
    impact_draw: float
    impact_away: float
    status: str = "completed"

class LogicChain(BaseModel):
    match_id: int
    steps: List[TraceStep] = []

    def add_step(self, name: str, desc: str, current_spf: Dict[str, float], delta: Dict[str, float] = None):
        self.steps.append(TraceStep(
            name=name,
            description=desc,
            impact_home=current_spf["home"],
            impact_draw=current_spf["draw"],
            impact_away=current_spf["away"]
        ))

