"""
特征生成层 — 把原始数据转为预测信号

子模型:
  EloModel         — 实力基线胜率
  PoissonModel     — 双变量泊松攻防模型 (Dixon-Coles 修正)
  PlayerAdjustmentModel — 球员状态修正
  MarketModel      — 赔率隐含概率 (含去水)
  FormAdjustmentModel   — 近期状态修正
  HomeAwayModel    — 主客场修正
  ScheduleDensityModel  — 赛程密度修正
  WeatherVenueModel     — 天气场地修正
  TacticalModel    — 战术风格相克
  CoachImpactModel — 教练临场能力
  SquadAvailabilityModel — 伤病停赛影响

所有模型保持纯函数风格，输入 MatchContext + TeamContext，输出概率/因子。
"""
from features.elo_model import EloModel
from features.poisson_model import PoissonModel
from features.adjustment_models import (
    PlayerAdjustmentModel,
    FormAdjustmentModel,
    HomeAwayModel,
    ScheduleDensityModel,
    WeatherVenueModel,
    TacticalModel,
    CoachImpactModel,
    SquadAvailabilityModel,
)
from features.market_model import MarketModel

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
]
