"""天气/场地修正模型 — 恶劣天气和糟糕场地的影响评估。"""

from core.context import MatchContext
from core.context import TeamContext


class WeatherVenueModel:
    """恶劣天气和糟糕场地对双方都有影响。"""

    WEATHER_PENALTY = {
        "clear": 0.00,
        "cloudy": 0.00,
        "rain": 0.04,
        "hot": 0.06,
        "cold": 0.04,
        "snow": 0.10,
    }
    PITCH_PENALTY = {
        "good": 0.00,
        "average": 0.02,
        "poor": 0.05,
        "artificial": 0.03,
    }

    @classmethod
    def compute_factor(cls, ctx: MatchContext, team: TeamContext) -> float:
        weather_pen = cls.WEATHER_PENALTY.get(ctx.weather, 0.0)
        pitch_pen = cls.PITCH_PENALTY.get(ctx.pitch_condition, 0.0)
        total_pen = weather_pen + pitch_pen

        adapt = team.weather_adaptability
        effective = total_pen * max(0.3, 1.5 - adapt * 0.5)
        return max(0.85, 1.0 - effective)
