"""
特征生成层 — 把原始数据转为预测信号

子模型已迁移到 core.models/ (向后兼容: features/__init__.py 重新导出)。
旧 features/*.py 文件保留供历史引用,但不再维护。

新代码请从 core.models 导入。

注意: 使用延迟导入避免循环依赖。
"""

# 延迟导入 — 只在首次访问时加载,避免 circular import
def __getattr__(name):
    if name == "EloModel":
        from core.models.elo import EloModel
        return EloModel
    if name == "PoissonModel":
        from core.models.poisson import PoissonModel
        return PoissonModel
    if name == "PlayerAdjustmentModel":
        from core.models.player_adjustment import PlayerAdjustmentModel
        return PlayerAdjustmentModel
    if name == "FormAdjustmentModel":
        from core.models.form_adjustment import FormAdjustmentModel
        return FormAdjustmentModel
    if name == "HomeAwayModel":
        from core.models.home_away import HomeAwayModel
        return HomeAwayModel
    if name == "ScheduleDensityModel":
        from core.models.schedule_density import ScheduleDensityModel
        return ScheduleDensityModel
    if name == "WeatherVenueModel":
        from core.models.weather_venue import WeatherVenueModel
        return WeatherVenueModel
    if name == "TacticalModel":
        from core.models.tactical import TacticalModel
        return TacticalModel
    if name == "CoachImpactModel":
        from core.models.coach_impact import CoachImpactModel
        return CoachImpactModel
    if name == "SquadAvailabilityModel":
        from core.models.squad_availability import SquadAvailabilityModel
        return SquadAvailabilityModel
    if name == "MarketModel":
        from core.models.market import MarketModel
        return MarketModel
    if name == "DrawDetectionModel":
        from core.models.draw_detection import DrawDetectionModel
        return DrawDetectionModel
    if name == "RefereeModel":
        from features.adjustment_models import RefereeModel
        return RefereeModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "RefereeModel",
    "MarketModel",
    "DrawDetectionModel",
]
