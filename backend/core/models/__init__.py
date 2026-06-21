"""
子模型包 — 所有 Layer 1 物理模型

向后兼容: 从 core.prediction_engine 导入仍然有效。
新代码请直接从这里导入。
"""

# 依赖顺序：poisson.py 引用了其他子模型，必须最后导入
from core.models.elo import EloModel
from core.models.player_adjustment import PlayerAdjustmentModel
from core.models.form_adjustment import FormAdjustmentModel
from core.models.home_away import HomeAwayModel
from core.models.schedule_density import ScheduleDensityModel
from core.models.weather_venue import WeatherVenueModel
from core.models.tactical import TacticalModel
from core.models.coach_impact import CoachImpactModel
from core.models.squad_availability import SquadAvailabilityModel
from core.models.market import MarketModel
from core.models.draw_detection import DrawDetectionModel
from core.models.poisson import PoissonModel

__all__ = [
    "EloModel",
    "PoissonModel",
    "PlayerAdjustmentModel",
    "FormAdjustmentModel",
    "HomeAwayModel",
    "ScheduleDensityModel",
    "WeatherVenueModel",
    "TacticalModel",
    "CoachImpactModel",
    "SquadAvailabilityModel",
    "MarketModel",
    "DrawDetectionModel",
]
