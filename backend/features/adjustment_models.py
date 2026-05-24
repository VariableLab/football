"""
修正模型集合 — 对 Elo/Poisson 基础预测的逐步修正

包含:
  PlayerAdjustmentModel   — 核心球员可用性/疲劳修正
  FormAdjustmentModel     — 近 N 场战绩状态因子
  HomeAwayModel           — 主客场差异
  ScheduleDensityModel    — 赛程密度/休息天数
  WeatherVenueModel       — 天气/场地影响
  TacticalModel           — 战术风格相克
  CoachImpactModel        — 教练临场能力
  SquadAvailabilityModel  — 伤病停赛影响
"""
from typing import Tuple


# ────────────────────────────
# 球员状态修正
# ────────────────────────────
class PlayerAdjustmentModel:
    """
    根据球员 availability 和疲劳度，输出一个战力修正系数 (0.7 ~ 1.3)。
    该系数直接乘到 Elo / 泊松的输出上。
    """

    POSITION_IMPACT = {
        "goalkeeper": 0.12,
        "defense": 0.08,
        "midfield": 0.07,
        "forward": 0.08,
    }

    @classmethod
    def predict(cls, ctx: "MatchContext") -> float:
        home = ctx.home_team
        away = ctx.away_team

        home_avail = home.key_players_available / max(home.key_players_total, 1)
        away_avail = away.key_players_available / max(away.key_players_total, 1)

        home_fatigue_penalty = home.squad_fatigue_index * 0.10
        away_fatigue_penalty = away.squad_fatigue_index * 0.10

        home_strength = home_avail * (1 - home_fatigue_penalty)
        away_strength = away_avail * (1 - away_fatigue_penalty)

        if away_strength == 0:
            return 1.3
        ratio = home_strength / away_strength
        return max(0.7, min(1.3, 0.85 + 0.15 * ratio))


# ────────────────────────────
# 近期状态修正
# ────────────────────────────
class FormAdjustmentModel:
    """
    根据近 N 场战绩（W/D/L 字符串）计算加权状态因子。
    最近一场权重最高，越往前权重递减。
    """

    @classmethod
    def compute_factor(cls, team: "TeamContext") -> float:
        if not team.recent_results:
            return 1.0

        results = team.recent_results[-10:]
        n = len(results)
        if n == 0:
            return 1.0

        weights = [0.5 + 0.5 * (i / max(n - 1, 1)) for i in range(n)]
        points_map = {"W": 3, "D": 1, "L": 0}
        points = [points_map.get(r.upper(), 1) for r in results]

        weighted_avg = sum(p * w for p, w in zip(points, weights)) / sum(weights)
        return max(0.75, min(1.15, 0.85 + 0.10 * (weighted_avg / 1.5)))


# ────────────────────────────
# 主客场修正
# ────────────────────────────
class HomeAwayModel:
    """主客场差异修正。世界杯是中立场，俱乐部比赛有主场优势。"""

    @classmethod
    def compute_factor(cls, ctx: "MatchContext", is_home: bool) -> float:
        if ctx.venue_type == "neutral":
            return 1.0

        team = ctx.home_team if is_home else ctx.away_team
        factor = team.home_away_factor
        if is_home:
            return factor
        return max(0.8, 2.0 - factor)


# ────────────────────────────
# 赛程密度 / 疲劳修正
# ────────────────────────────
class ScheduleDensityModel:
    """休息天数不足 → 攻防效率下降"""

    @classmethod
    def compute_factor(cls, team: "TeamContext") -> float:
        rest = team.rest_days
        fatigue = team.squad_fatigue_index

        rest_penalty = {
            (5, 999): 1.00,
            (3, 5): 0.97,
            (2, 3): 0.92,
            (0, 2): 0.85,
        }
        rest_mult = 0.85
        for (low, high), val in rest_penalty.items():
            if low <= rest < high:
                rest_mult = val
                break

        fatigue_mult = 1.0 - 0.15 * fatigue
        return max(0.80, rest_mult * fatigue_mult)


# ────────────────────────────
# 天气 / 场地修正
# ────────────────────────────
class WeatherVenueModel:
    """恶劣天气和糟糕场地的影响，气候适应性强的球队受影响更小。"""

    WEATHER_PENALTY = {
        "clear": 0.00, "cloudy": 0.00, "rain": 0.04,
        "hot": 0.06, "cold": 0.04, "snow": 0.10,
    }
    PITCH_PENALTY = {
        "good": 0.00, "average": 0.02, "poor": 0.05, "artificial": 0.03,
    }

    @classmethod
    def compute_factor(cls, ctx: "MatchContext", team: "TeamContext") -> float:
        weather_pen = cls.WEATHER_PENALTY.get(ctx.weather, 0.0)
        pitch_pen = cls.PITCH_PENALTY.get(ctx.pitch_condition, 0.0)
        total_pen = weather_pen + pitch_pen

        adapt = team.weather_adaptability
        effective = total_pen * max(0.3, 1.5 - adapt * 0.5)
        return max(0.85, 1.0 - effective)


# ────────────────────────────
# 战术风格相克
# ────────────────────────────
class TacticalModel:
    """不同战术风格相遇时的进球预期修正。"""

    TACTICAL_MATRIX = {
        ("attack", "attack"): (1.15, 1.15),
        ("attack", "defense"): (0.88, 0.72),
        ("attack", "balanced"): (1.02, 0.92),
        ("attack", "counter"): (0.95, 1.12),
        ("defense", "attack"): (0.72, 0.88),
        ("defense", "defense"): (0.68, 0.68),
        ("defense", "balanced"): (0.80, 0.85),
        ("defense", "counter"): (0.62, 0.75),
        ("balanced", "attack"): (0.92, 1.02),
        ("balanced", "defense"): (0.85, 0.80),
        ("balanced", "balanced"): (1.00, 1.00),
        ("balanced", "counter"): (0.95, 0.95),
        ("counter", "attack"): (1.12, 0.95),
        ("counter", "defense"): (0.75, 0.62),
        ("counter", "balanced"): (0.95, 0.95),
        ("counter", "counter"): (0.85, 0.85),
    }

    @classmethod
    def compute_factors(cls, ctx: "MatchContext") -> Tuple[float, float]:
        home_s = (ctx.home_team.tactical_style or "balanced").lower()
        away_s = (ctx.away_team.tactical_style or "balanced").lower()
        base = cls.TACTICAL_MATRIX.get((home_s, away_s), (1.0, 1.0))

        h_poss = ctx.home_team.possession
        a_poss = ctx.away_team.possession
        if h_poss > 0 and a_poss > 0:
            poss_diff = (h_poss - a_poss) / 100.0
            adj = poss_diff * 0.15
            return (base[0] * (1 + adj), base[1] * (1 - adj))
        return base


# ────────────────────────────
# 教练临场能力
# ────────────────────────────
class CoachImpactModel:
    """高评分教练在关键时刻能提升球队进攻效率 3~6%。"""

    @classmethod
    def compute_factor(cls, team: "TeamContext", is_knockout: bool = False) -> float:
        rating = team.coach_rating
        base = 1.0 + 0.04 * (rating - 0.5)
        if is_knockout:
            base += 0.03 * (rating - 0.5)
        return max(0.90, min(1.10, base))


# ────────────────────────────
# 阵容完整度（伤病停赛）
# ────────────────────────────
class SquadAvailabilityModel:
    """核心球员伤停对攻防的影响。"""

    @classmethod
    def compute_factor(cls, team: "TeamContext") -> Tuple[float, float]:
        """返回 (进攻乘数, 防守乘数)"""
        if not team.key_injuries:
            return 1.0, 1.0

        injuries = [x.strip() for x in team.key_injuries.split(",") if x.strip()]
        count = len(injuries)

        attack_pen = min(0.15, count * 0.03)
        defense_pen = min(0.12, count * 0.02)

        fatigue_mult = 1.0 + 0.5 * team.squad_fatigue_index
        attack_pen *= fatigue_mult
        defense_pen *= fatigue_mult

        return max(0.80, 1.0 - attack_pen), max(0.85, 1.0 - defense_pen)


# ────────────────────────────
# 裁判因素修正
# ────────────────────────────
class RefereeModel:
    """裁判风格修正：某些裁判出牌多、尺度严，可能影响比赛节奏。"""

    @classmethod
    def compute_factor(cls, ctx: "MatchContext") -> Tuple[float, float]:
        """返回 (主队λ修正, 客队λ修正)"""
        # 简化版实现：主要影响进球数（尺度严 -> 进球变少）
        ref = getattr(ctx, "referee", None)
        if not ref:
            return 1.0, 1.0
        
        cards_per_game = getattr(ref, "yellow_cards_avg", 4.0)
        # 严厉度因子：高于4张为严，低于3张为松
        severity = max(0.8, min(1.2, cards_per_game / 4.0))
        
        # 尺度严通常会导致比赛支离破碎，进球略微减少 (0.95 ~ 1.05)
        goal_mult = 1.0 + (1.0 - severity) * 0.15
        
        return max(0.90, goal_mult), max(0.90, goal_mult)

