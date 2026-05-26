"""
世界杯预测引擎

包含：
  - Elo 实力模型
  - 泊松攻防模型（双变量）
  - 球员状态修正
  - 市场赔率隐含概率
  - 线性融合层
  - 回测框架

用法：
    engine = PredictionEngine()
    result = engine.predict(match, context)
    # result 包含全部 6 种玩法的概率分布
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
from itertools import product

import numpy as np
from scipy.stats import poisson

from models import PlayType
import yaml
import os

# ─── 子模型已迁移到 features/ 包（向后兼容：本地定义仍可用）───
from features import (
    EloModel, PoissonModel, PlayerAdjustmentModel,
    FormAdjustmentModel, HomeAwayModel, ScheduleDensityModel,
    WeatherVenueModel, TacticalModel, CoachImpactModel, SquadAvailabilityModel,
    MarketModel, RefereeModel,
)

# ─── LR 融合层 (v2 架构) ───
# 注意：直接从 logistic_fusion 导入，不经过 fusion/__init__.py，避免循环导入
# (fusion/__init__.py → fusion_trainer.py → prediction_engine 循环)
import fusion.logistic_fusion as _lr_module
LogisticFusionWeights = _lr_module.LogisticFusionWeights
from features.feature_builder import FeatureBuilder
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel

# ────────────────────────────
# 常量 / 配置（支持 YAML 动态加载）
# ────────────────────────────
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "model_config.yaml")

def load_engine_config():
    # 默认值
    cfg = {
        "MAX_GOALS": 8,
        "POISSON_TRUNCATE": 0.999,
        "HOME_ADVANTAGE_ELO": 0,
        "FORM_WINDOW_MATCHES": 10,
        "DIXON_COLES_RHO": 0.0092,
        "DRAW_INFLATION_FACTOR": 1.27,
        "DEFAULT_WEIGHTS": {
            "elo": 0.05,
            "poisson": 0.13,
            "players": 0.19,
            "market": 0.63,
        }
    }
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                external_cfg = yaml.safe_load(f)
                if external_cfg:
                    cfg.update(external_cfg)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[config] Failed to load model_config.yaml: {e}")
    return cfg

# 加载当前配置
_ENGINE_CFG = load_engine_config()

MAX_GOALS = _ENGINE_CFG["MAX_GOALS"]
POISSON_TRUNCATE = _ENGINE_CFG["POISSON_TRUNCATE"]
HOME_ADVANTAGE_ELO = _ENGINE_CFG["HOME_ADVANTAGE_ELO"]
FORM_WINDOW_MATCHES = _ENGINE_CFG["FORM_WINDOW_MATCHES"]
DIXON_COLES_RHO = _ENGINE_CFG["DIXON_COLES_RHO"]
DRAW_INFLATION_FACTOR = _ENGINE_CFG["DRAW_INFLATION_FACTOR"]
DEFAULT_WEIGHTS = _ENGINE_CFG["DEFAULT_WEIGHTS"]



# ────────────────────────────
# 数据结构
# ────────────────────────────
@dataclass
class TeamContext:
    """一支球队的赛前上下文"""
    team_id: int
    name: str
    elo: int = 1500
    fifa_rank: int = 100

    # 近 N 场场均数据（用于泊松参数）
    avg_goals_scored: float = 1.30
    avg_goals_conceded: float = 1.10

    # ─── FBref / SoccerData 高级统计（自动同步） ───
    avg_xg: float = 0.0              # 场均期望进球 xG（优先于 avg_goals_scored）
    avg_xga: float = 0.0             # 场均期望失球 xGA
    possession: float = 50.0         # 平均控球率 %
    pass_completion: float = 0.0     # 传球成功率 %
    shots_per_game: float = 0.0      # 场均射门次数

    # 状态因子 (0.5 ~ 1.5)
    form_factor: float = 1.0

    # 球员相关
    key_players_available: int = 11
    key_players_total: int = 11
    squad_fatigue_index: float = 0.5

    # 本届赛事已赛场次
    tournament_matches_played: int = 0
    tournament_goals_scored: int = 0
    tournament_goals_conceded: int = 0

    # ─── 扩展：近期战绩 ───
    recent_results: str = ""           # e.g. "WWDLWLLDWD"
    recent_goals_scored: float = 0.0
    recent_goals_conceded: float = 0.0

    # ─── 扩展：主客场 / 气候 ───
    home_away_factor: float = 1.0      # 1.0=中性, >1 主场优势大
    weather_adaptability: float = 1.0  # 0~2

    # ─── 扩展：战术与教练 ───
    tactical_style: str = "balanced"   # attack / defense / balanced / counter
    coach_rating: float = 0.5          # 0~1

    # ─── 扩展：赛程与疲劳 ───
    rest_days: int = 7
    key_injuries: str = ""             # e.g. "梅西(伤),内马尔(停)"


@dataclass
class RefereeContext:
    name: str
    yellow_cards_avg: float = 4.0
    red_cards_avg: float = 0.2
    home_win_bias: float = 1.0  # >1 means home teams win more under this ref


@dataclass
class MatchContext:
    """单场比赛的完整上下文"""
    match_id: int
    home_team: TeamContext
    away_team: TeamContext
    referee: Optional[RefereeContext] = None

    # 赛事阶段
    stage: str = "group"   # group / R32 / R16 / QF / SF / F
    is_knockout: bool = False

    # 让球（竞彩官方让球）
    handicap: int = 0

    # 市场赔率（当前最佳估计，可能包含合成赔率）
    odds_home: Optional[float] = None
    odds_draw: Optional[float] = None
    odds_away: Optional[float] = None

    # ─── 收盘赔率（真实市场赔率，赛前最后采集） ───
    # 关键设计：closing_odds 只来自真实数据源（oddsapi / betexplorer / football-data）
    # synthetic 赔率不入 closing_odds，消除循环引用
    closing_odds_home: Optional[float] = None
    closing_odds_draw: Optional[float] = None
    closing_odds_away: Optional[float] = None

    # 历史交锋（近5场对战结果）
    h2h_home_wins: int = 0
    h2h_draws: int = 0
    h2h_away_wins: int = 0

    # 特殊标记
    is_third_round_group: bool = False
    is_late_season: bool = False       # 是否为赛季冲刺阶段 (Top/Bottom 抢分)
    home_team_qualified: Optional[bool] = None
    away_team_qualified: Optional[bool] = None

    # ─── 扩展：比赛环境 ───
    venue_type: str = "neutral"        # home / away / neutral
    weather: str = "clear"             # clear / rain / hot / cold / snow
    temperature: float = 20.0
    pitch_condition: str = "good"      # good / average / poor / artificial
    schedule_density: str = "normal"   # light / normal / dense / extreme
    competition: str = ""              # 赛事名称 (e.g. EPL, LaLiga)

    @property
    def has_odds(self) -> bool:
        return self.odds_home is not None and self.odds_draw is not None and self.odds_away is not None

    @property
    def has_closing_odds(self) -> bool:
        """是否有真实收盘赔率（非合成）"""
        return (
            self.closing_odds_home is not None
            and self.closing_odds_draw is not None
            and self.closing_odds_away is not None
        )


@dataclass
class PredictionResult:
    """单场比赛的完整预测结果"""
    match_id: int

    # 融合后最终概率
    spf: Dict[str, float] = field(default_factory=dict)       # 胜平负
    rq: Dict[str, float] = field(default_factory=dict)        # 让球胜平负
    score: Dict[str, float] = field(default_factory=dict)     # 比分
    goals: Dict[str, float] = field(default_factory=dict)     # 总进球
    half: Dict[str, float] = field(default_factory=dict)      # 半全场

    # 子模型原始输出（用于可解释性拆解）
    raw_elo: Dict[str, float] = field(default_factory=dict)
    raw_poisson: Dict[str, float] = field(default_factory=dict)
    raw_players: float = 0.0
    raw_market: Dict[str, float] = field(default_factory=dict)

    # 元信息
    model_version: str = "v1.0"
    confidence: str = "medium"   # high / medium / low
    odds_degraded: bool = False  # True when prediction lacks market odds
    weights_used: Dict[str, Any] = field(default_factory=dict)
    trace: Optional[Any] = None  # LogicChain object

    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_db_payload(self) -> List[Dict[str, Any]]:
        return [
            {"play_type": PlayType.SPF, "probabilities": self.spf, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.RQ, "probabilities": self.rq, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.SCORE, "probabilities": self.score, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.GOALS, "probabilities": self.goals, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.HALF, "probabilities": self.half, "confidence": self.confidence, "model_version": self.model_version},
        ]


# ────────────────────────────
# 模型 A：Elo 实力模型
# ────────────────────────────
class EloModel:
    """
    Elo 评分系统，输出胜平负概率。
    参考 FiveThirtyEight 的 Soccer SPI 方法，加入平局修正。
    """

    # 世界杯经验参数：Elo 差对应的胜率
    @staticmethod
    def win_prob(elo_diff: float) -> float:
        return 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

    @classmethod
    def predict(cls, ctx: MatchContext) -> Dict[str, float]:
        diff = ctx.home_team.elo - ctx.away_team.elo + HOME_ADVANTAGE_ELO

        # 基础胜率
        p_win = cls.win_prob(diff)
        p_loss = cls.win_prob(-diff)

        # 平局修正：Elo 接近时平局概率上升
        # 经验公式：平局概率 ~ 0.25 + 0.1 * exp(-|diff|/200)
        draw_base = 0.25 + 0.10 * math.exp(-abs(diff) / 200.0)

        # 淘汰赛平局概率更高（保守）
        if ctx.is_knockout:
            draw_base += 0.08

        # 归一化
        total = p_win + draw_base + p_loss
        return {
            "home": p_win / total,
            "draw": draw_base / total,
            "away": p_loss / total,
        }


# ────────────────────────────
# 模型 B：泊松双变量模型
# ────────────────────────────
class PoissonModel:
    """
    双变量泊松模型，输出：
      - 具体比分概率
      - 胜平负概率（由比分加总）
      - 总进球概率
      - 让球概率
      - 半全场概率（简化版）
    """

    @classmethod
    def _compute_lambdas(cls, ctx: MatchContext) -> Tuple[float, float]:
        """计算双方期望进球 λ（全维度修正版）"""
        # ── 1. 联赛基准进攻/防守强度 ──
        # 优先使用 FBref xG/xGA（更稳定的预测信号），fallback 到实际进球数
        home_attack = ctx.home_team.avg_xg if ctx.home_team.avg_xg > 0 else ctx.home_team.avg_goals_scored
        home_defense = ctx.home_team.avg_xga if ctx.home_team.avg_xga > 0 else ctx.home_team.avg_goals_conceded
        away_attack = ctx.away_team.avg_xg if ctx.away_team.avg_xg > 0 else ctx.away_team.avg_goals_scored
        away_defense = ctx.away_team.avg_xga if ctx.away_team.avg_xga > 0 else ctx.away_team.avg_goals_conceded

        # ── 2. 本届赛事动态修正 ──
        if ctx.home_team.tournament_matches_played > 0:
            home_attack = 0.6 * home_attack + 0.4 * (
                ctx.home_team.tournament_goals_scored / max(ctx.home_team.tournament_matches_played, 1)
            )
        if ctx.away_team.tournament_matches_played > 0:
            away_attack = 0.6 * away_attack + 0.4 * (
                ctx.away_team.tournament_goals_scored / max(ctx.away_team.tournament_matches_played, 1)
            )

        # ── 3. 中立场优势（排名高的一方略优）──
        neutral_advantage = 1.0
        if ctx.home_team.fifa_rank < ctx.away_team.fifa_rank:
            neutral_advantage = 1.05
        elif ctx.home_team.fifa_rank > ctx.away_team.fifa_rank:
            neutral_advantage = 0.95

        # ── 4. 基础 λ ──
        lambda_home = home_attack * away_defense * ctx.home_team.form_factor * neutral_advantage
        lambda_away = away_attack * home_defense * ctx.away_team.form_factor * (1.0 / neutral_advantage)

        # ── 5. 近期状态修正 ──
        lambda_home *= FormAdjustmentModel.compute_factor(ctx.home_team)
        lambda_away *= FormAdjustmentModel.compute_factor(ctx.away_team)

        # ── 6. 主客场修正 ──
        lambda_home *= HomeAwayModel.compute_factor(ctx, is_home=True)
        lambda_away *= HomeAwayModel.compute_factor(ctx, is_home=False)

        # ── 7. 赛程密度 / 疲劳修正 ──
        lambda_home *= ScheduleDensityModel.compute_factor(ctx.home_team)
        lambda_away *= ScheduleDensityModel.compute_factor(ctx.away_team)

        # ── 8. 天气 / 场地修正 ──
        lambda_home *= WeatherVenueModel.compute_factor(ctx, ctx.home_team)
        lambda_away *= WeatherVenueModel.compute_factor(ctx, ctx.away_team)

        # ── 9. 战术风格相克 ──
        tact_h, tact_a = TacticalModel.compute_factors(ctx)
        lambda_home *= tact_h
        lambda_away *= tact_a

        # ── 10. 教练临场能力 ──
        lambda_home *= CoachImpactModel.compute_factor(ctx.home_team, ctx.is_knockout)
        lambda_away *= CoachImpactModel.compute_factor(ctx.away_team, ctx.is_knockout)

        # ── 11. 阵容完整度（伤病停赛）──
        home_atk_pen, home_def_pen = SquadAvailabilityModel.compute_factor(ctx.home_team)
        away_atk_pen, away_def_pen = SquadAvailabilityModel.compute_factor(ctx.away_team)
        # 进攻方受自身伤病影响，防守方受对方伤病影响
        lambda_home *= home_atk_pen * away_def_pen
        lambda_away *= away_atk_pen * home_def_pen

        # ── 12. 裁判因素修正 ──
        ref_h, ref_a = RefereeModel.compute_factor(ctx)
        lambda_home *= ref_h
        lambda_away *= ref_a

        # ── 13. 淘汰赛修正：进球数下降（分阶段细化） ──
        if ctx.is_knockout:
            stage_factor = {"R16": 0.88, "QF": 0.85, "SF": 0.82, "F": 0.80, "3P": 0.90}
            factor = stage_factor.get(ctx.stage, 0.85)
            lambda_home *= factor
            lambda_away *= factor

        # ── 13. 第三轮小组赛轮换修正 ──
        if ctx.is_third_round_group:
            if ctx.home_team_qualified is True or ctx.away_team_qualified is True:
                lambda_home *= 0.90
                lambda_away *= 0.90

        return max(lambda_home, 0.1), max(lambda_away, 0.1)

    @staticmethod
    def _tau_dixon_coles(i: int, j: int, lambda_h: float, lambda_a: float, rho: float) -> float:
        """
        Dixon-Coles 相关性修正因子 tau(i,j)。
        仅对低比分 (0,0), (1,0), (0,1), (1,1) 进行修正，其余为 1。
        公式（Dixon & Coles, 1997）:
          tau(0,0) = 1 - lambda_h * lambda_a * rho
          tau(1,0) = 1 + lambda_h * rho
          tau(0,1) = 1 + lambda_a * rho
          tau(1,1) = 1 - rho
          tau(i,j) = 1               当 i>1 或 j>1
        rho 为负值时表示低比分间的负相关（足球典型值 -0.05 ~ -0.15）。
        """
        if i == 0 and j == 0:
            return 1.0 - lambda_h * lambda_a * rho
        elif i == 1 and j == 0:
            return 1.0 + lambda_h * rho
        elif i == 0 and j == 1:
            return 1.0 + lambda_a * rho
        elif i == 1 and j == 1:
            return 1.0 - rho
        return 1.0

    @classmethod
    def predict_score_matrix(cls, ctx: MatchContext, rho: float = DIXON_COLES_RHO) -> np.ndarray:
        """
        返回 (MAX_GOALS+1) × (MAX_GOALS+1) 的比分概率矩阵。
        score_matrix[i][j] = P(主队进 i 球，客队进 j 球)

        使用 Dixon-Coles 修正替代独立泊松，更准确地校准低比分概率。
        """
        lambda_h, lambda_a = cls._compute_lambdas(ctx)

        size = MAX_GOALS + 1
        matrix = np.zeros((size, size))

        for i in range(size):
            for j in range(size):
                # 基础泊松概率
                if i < MAX_GOALS and j < MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * poisson.pmf(j, lambda_a)
                elif i == MAX_GOALS and j < MAX_GOALS:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * poisson.pmf(j, lambda_a)
                elif i < MAX_GOALS and j == MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))
                else:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))

                # Dixon-Coles 低比分相关性修正
                tau = cls._tau_dixon_coles(i, j, lambda_h, lambda_a, rho)
                matrix[i][j] = tau * base

        # 归一化（确保总和为1）
        total = matrix.sum()
        if total > 0:
            matrix /= total
        return matrix, lambda_h, lambda_a

    @classmethod
    def predict_spf_only(cls, ctx: MatchContext) -> Dict[str, float]:
        """只计算胜平负概率，跳过比分/进球等（用于权重学习加速）"""
        lambda_h, lambda_a = cls._compute_lambdas(ctx)
        size = MAX_GOALS + 1

        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0

        for i in range(size):
            for j in range(size):
                if i < MAX_GOALS and j < MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * poisson.pmf(j, lambda_a)
                elif i == MAX_GOALS and j < MAX_GOALS:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * poisson.pmf(j, lambda_a)
                elif i < MAX_GOALS and j == MAX_GOALS:
                    base = poisson.pmf(i, lambda_h) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))
                else:
                    base = (1 - poisson.cdf(MAX_GOALS - 1, lambda_h)) * (1 - poisson.cdf(MAX_GOALS - 1, lambda_a))

                tau = cls._tau_dixon_coles(i, j, lambda_h, lambda_a, DIXON_COLES_RHO)
                prob = tau * base

                if i > j:
                    p_home += prob
                elif i == j:
                    p_draw += prob
                else:
                    p_away += prob

        # 平局膨胀修正
        p_draw *= DRAW_INFLATION_FACTOR
        total = p_home + p_draw + p_away
        return {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}

    @classmethod
    def predict(cls, ctx: MatchContext) -> Dict[str, Any]:
        """返回泊松模型的全部玩法预测"""
        matrix, lambda_h, lambda_a = cls.predict_score_matrix(ctx)
        size = matrix.shape[0]

        # 1. 胜平负（由比分矩阵加总 + 平局膨胀修正）
        p_home = sum(matrix[i][j] for i in range(size) for j in range(size) if i > j)
        p_draw = sum(matrix[i][j] for i in range(size) for j in range(size) if i == j)
        p_away = sum(matrix[i][j] for i in range(size) for j in range(size) if i < j)

        # 平局膨胀修正：独立泊松系统低估平局
        p_draw *= DRAW_INFLATION_FACTOR
        total = p_home + p_draw + p_away

        spf = {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}

        # 2. 比分（仅输出概率 > 1% 的）
        # 对平局格子(1:1等)也应用DRAW_INFLATION_FACTOR，使比分与SPF标度一致
        inflated_matrix = np.copy(matrix)
        for i in range(size):
            inflated_matrix[i][i] *= DRAW_INFLATION_FACTOR
        # 归一化膨胀后的比分矩阵
        inflated_sum = inflated_matrix.sum()
        if inflated_sum > 0:
            inflated_matrix /= inflated_sum

        score = {}
        for i in range(size):
            for j in range(size):
                key = f"{i}:{j}" if i < MAX_GOALS and j < MAX_GOALS else f"{min(i, MAX_GOALS)}+:{min(j, MAX_GOALS)}+"
                prob = inflated_matrix[i][j]
                if prob > 0.01:
                    score[f"{i}:{j}"] = round(prob, 4)

        # 3. 总进球（0-6 独立桶，7+ 尾部累积）
        goals = {}
        for total_goals in range(7):
            prob = sum(matrix[i][j] for i in range(size) for j in range(size) if i + j == total_goals)
            if prob > 0.005:
                goals[str(total_goals)] = round(prob, 4)
        # 7+ 累积所有进球>=7的概率
        prob_7plus = sum(matrix[i][j] for i in range(size) for j in range(size) if i + j >= 7)
        if prob_7plus > 0.005:
            goals["7+"] = round(prob_7plus, 4)

        # 4. 让球胜平负
        # 竞彩让球 N：主队需要净胜 > N 球才算让胜
        handicap = ctx.handicap
        p_rq_home = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) > handicap)
        p_rq_draw = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) == handicap)
        p_rq_away = sum(matrix[i][j] for i in range(size) for j in range(size) if (i - j) < handicap)
        rq_total = p_rq_home + p_rq_draw + p_rq_away

        rq = {
            "home": p_rq_home / rq_total,
            "draw": p_rq_draw / rq_total,
            "away": p_rq_away / rq_total,
            "handicap": handicap,
        }

                # 5. 半全场（HT→FT 转移矩阵校准版）
        # 基于 23576 场历史数据统计的半场→全场转移概率
        # 校准日期: 2026-05-09
        HT_FT_TRANSITION = {
            "home":   {"home": 0.785, "draw": 0.151, "away": 0.065},
            "draw":   {"home": 0.442, "draw": 0.237, "away": 0.321},
            "away":   {"home": 0.105, "draw": 0.199, "away": 0.697},
        }
        # 实际半场结果分布（23,576 场统计）
        HT_DISTRIBUTION = {"home": 0.368, "draw": 0.364, "away": 0.268}

        # 上半场 λ = 全场 λ * 0.48（半场时间约占全场 48%）
        lambda_h_1h = lambda_h * 0.48
        lambda_a_1h = lambda_a * 0.48

        def half_outcome_prob(lh: float, la: float) -> Dict[str, float]:
            """计算半场结果概率"""
            p_h = 0.0
            p_d = 0.0
            p_a = 0.0
            for i in range(5):
                for j in range(5):
                    pi = poisson.pmf(i, lh)
                    pj = poisson.pmf(j, la)
                    if i > j:
                        p_h += pi * pj
                    elif i == j:
                        p_d += pi * pj
                    else:
                        p_a += pi * pj
            t = p_h + p_d + p_a
            return {"home": p_h / t, "draw": p_d / t, "away": p_a / t}

        # 泊松模型估计半场结果分布
        half_1h = half_outcome_prob(lambda_h_1h, lambda_a_1h)

        # 混合: 50% 泊松估计 + 50% 历史先验（避免极端偏差）
        for k in half_1h:
            half_1h[k] = 0.5 * half_1h[k] + 0.5 * HT_DISTRIBUTION[k]

        # P(半场=X, 全场=Y) = P(半场=X) * P(全场=Y | 半场=X)
        # 使用校准后的转移矩阵
        half = {}
        outcomes = ["home", "draw", "away"]
        labels = {"homehome": "主主", "homedraw": "主平", "homeaway": "主客",
                  "drawhome": "平主", "drawdraw": "平平", "drawaway": "平客",
                  "awayhome": "客主", "awaydraw": "客平", "awayaway": "客客"}
        for h1 in outcomes:
            for h2 in outcomes:
                key = f"{h1}{h2}"
                prob = half_1h[h1] * HT_FT_TRANSITION[h1][h2]
                half[labels.get(key, key)] = prob

        # 归一化
        half_total = sum(half.values())
        half = {k: round(v / half_total, 4) for k, v in half.items()}

        return {
            "spf": spf,
            "rq": rq,
            "score": score,
            "goals": goals,
            "half": half,
            "lambda_home": lambda_h,
            "lambda_away": lambda_a,
        }


# ────────────────────────────
# 模型 C：球员状态修正
# ────────────────────────────
class PlayerAdjustmentModel:
    """
    根据球员 availability 和疲劳度，输出一个战力修正系数。
    该系数直接乘到 Elo / 泊松的输出上，不单独输出概率分布。
    """

    # 核心位置缺阵的战力损失（经验值）
    POSITION_IMPACT = {
        "goalkeeper": 0.12,
        "defense": 0.08,
        "midfield": 0.07,
        "forward": 0.08,
    }

    @classmethod
    def predict(cls, ctx: MatchContext) -> float:
        """返回 0.7 ~ 1.3 的战力修正系数"""
        home = ctx.home_team
        away = ctx.away_team

        # 可用核心球员比例
        home_avail = home.key_players_available / max(home.key_players_total, 1)
        away_avail = away.key_players_available / max(away.key_players_total, 1)

        # 疲劳影响（疲劳指数 0~1，指数越高战力越低）
        home_fatigue_penalty = home.squad_fatigue_index * 0.10  # 最大扣10%
        away_fatigue_penalty = away.squad_fatigue_index * 0.10

        # 综合修正：主队相对客队的战力比
        home_strength = home_avail * (1 - home_fatigue_penalty)
        away_strength = away_avail * (1 - away_fatigue_penalty)

        # 返回一个对称的修正因子：1.0 表示无差别，>1 主队更强，<1 客队更强
        if away_strength == 0:
            return 1.3
        ratio = home_strength / away_strength
        # 压缩到合理范围
        return max(0.7, min(1.3, 0.85 + 0.15 * ratio))


# ────────────────────────────
# 模型 D：近期状态修正
# ────────────────────────────
class FormAdjustmentModel:
    """
    根据近 N 场战绩（W/D/L 字符串）计算加权状态因子。
    最近一场权重最高，越往前权重递减。
    """
    @classmethod
    def compute_factor(cls, team: TeamContext) -> float:
        if not team.recent_results:
            return 1.0

        results = team.recent_results[-10:]  # 取最近 10 场
        n = len(results)
        if n == 0:
            return 1.0

        # 权重：最近一场 = 1.0，最早一场 = 0.5
        weights = [0.5 + 0.5 * (i / max(n - 1, 1)) for i in range(n)]
        points_map = {"W": 3, "D": 1, "L": 0}
        points = [points_map.get(r.upper(), 1) for r in results]

        weighted_avg = sum(p * w for p, w in zip(points, weights)) / sum(weights)
        # 1.5 分 = 中性(1.0), 3 分 = +10%, 0 分 = -15%
        return max(0.75, min(1.15, 0.85 + 0.10 * (weighted_avg / 1.5)))


# ────────────────────────────
# 模型 E：主客场修正
# ────────────────────────────
class HomeAwayModel:
    """
    主客场差异修正。
    世界杯是中立场，但存在气候/时差适应优势。
    """
    @classmethod
    def compute_factor(cls, ctx: MatchContext, is_home: bool) -> float:
        if ctx.venue_type == "neutral":
            return 1.0

        team = ctx.home_team if is_home else ctx.away_team
        factor = team.home_away_factor
        if is_home:
            return factor
        # 客场 = 反向打折
        return max(0.8, 2.0 - factor)


# ────────────────────────────
# 模型 F：赛程密度 / 疲劳修正
# ────────────────────────────
class ScheduleDensityModel:
    """
    休息天数不足 → 进攻/防守效率下降。
     also considers squad_fatigue_index.
    """
    @classmethod
    def compute_factor(cls, team: TeamContext) -> float:
        rest = team.rest_days
        fatigue = team.squad_fatigue_index

        # 休息天数惩罚
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

        # 疲劳指数惩罚 (0~1)
        fatigue_mult = 1.0 - 0.15 * fatigue

        return max(0.80, rest_mult * fatigue_mult)


# ────────────────────────────
# 模型 G：天气 / 场地修正
# ────────────────────────────
class WeatherVenueModel:
    """
    恶劣天气和糟糕场地对双方都有影响，
    但气候适应性强的球队受影响更小。
    """
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
        # 适应性越高，惩罚越小（适应性 1.0 = 正常，2.0 = 免疫）
        effective = total_pen * max(0.3, 1.5 - adapt * 0.5)
        return max(0.85, 1.0 - effective)


# ────────────────────────────
# 模型 H：战术风格相克
# ────────────────────────────
class TacticalModel:
    """
    不同战术风格相遇时的进球预期修正。
    这是一个经验矩阵，基于足球战术学常识。
    """
    # (主队风格, 客队风格) -> (主队进攻乘数, 客队进攻乘数)
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
    def compute_factors(cls, ctx: MatchContext) -> Tuple[float, float]:
        home_s = (ctx.home_team.tactical_style or "balanced").lower()
        away_s = (ctx.away_team.tactical_style or "balanced").lower()
        base = cls.TACTICAL_MATRIX.get((home_s, away_s), (1.0, 1.0))

        # ─── possession 数据校准 ───
        # 如果 FBref possession 数据可用，用它微调进攻乘数
        # 高控球率(>55%) → 轻微提升进攻预期（控制比赛节奏）
        # 低控球率(<45%) → 轻微降低进攻预期（反击型球队机会更少但质量更高，已体现在战术矩阵中）
        h_poss = ctx.home_team.possession
        a_poss = ctx.away_team.possession
        if h_poss > 0 and a_poss > 0:
            poss_diff = (h_poss - a_poss) / 100.0  # -0.5 ~ +0.5
            # 控球优势每多 10% → 进攻预期 +1.5%
            adj = poss_diff * 0.15
            return (base[0] * (1 + adj), base[1] * (1 - adj))
        return base


# ────────────────────────────
# 模型 I：教练临场能力
# ────────────────────────────
class CoachImpactModel:
    """
    高评分教练在关键时刻（淘汰赛、落后时）
    能提升球队进攻效率 3~6%。
    """
    @classmethod
    def compute_factor(cls, team: TeamContext, is_knockout: bool = False) -> float:
        rating = team.coach_rating  # 0~1
        base = 1.0 + 0.04 * (rating - 0.5)  # 0.5 = 中性
        if is_knockout:
            base += 0.03 * (rating - 0.5)
        return max(0.90, min(1.10, base))


# ────────────────────────────
# 模型 J：阵容完整度（伤病停赛）
# ────────────────────────────
class SquadAvailabilityModel:
    """
    核心球员伤停对攻防的影响。
    关键球员缺阵越多，进攻和防守双双下降。
    """
    @classmethod
    def compute_factor(cls, team: TeamContext) -> Tuple[float, float]:
        """返回 (进攻乘数, 防守乘数)"""
        if not team.key_injuries:
            return 1.0, 1.0

        # 解析伤病名单，统计人数
        injuries = [x.strip() for x in team.key_injuries.split(",") if x.strip()]
        count = len(injuries)

        # 每缺一个核心：进攻 -3%, 防守 -2%
        attack_pen = min(0.15, count * 0.03)
        defense_pen = min(0.12, count * 0.02)

        # 疲劳进一步放大影响
        fatigue_mult = 1.0 + 0.5 * team.squad_fatigue_index
        attack_pen *= fatigue_mult
        defense_pen *= fatigue_mult

        return max(0.80, 1.0 - attack_pen), max(0.85, 1.0 - defense_pen)


# ────────────────────────────
# 模型 D：市场赔率隐含概率
# ────────────────────────────
class MarketModel:
    """
    从市场赔率反推隐含概率。
    使用基础归一化（去除抽水），保留市场原始信号强度。
    世界杯淘汰赛阶段赔率来自欧洲主流博彩，返奖率 ~92-95%。
    """

    @classmethod
    def predict(cls, ctx: MatchContext) -> Optional[Dict[str, float]]:
        # ─── 关键修复：只使用真实收盘赔率 ───
        # 合成赔率（synthetic）是从 Elo 反推的，如果市场模型使用它，
        # 会形成循环引用：Elo → SyntheticOdds → MarketModel → Elo的噪声版本
        # 这会让 market 权重完全失效，甚至引入负向信号。
        # 因此 MarketModel 只在存在真实收盘赔率时才输出概率。
        if ctx.has_closing_odds:
            o1, oX, o2 = ctx.closing_odds_home, ctx.closing_odds_draw, ctx.closing_odds_away
        elif ctx.has_odds:
            # 兼容旧数据：如果没有独立的 closing_odds，降级使用普通 odds
            # 但会打日志提醒，因为普通 odds 可能包含合成值
            o1, oX, o2 = ctx.odds_home, ctx.odds_draw, ctx.odds_away
        else:
            return None

        # 基础归一化：1/odds 去除抽水
        raw = {"home": 1.0 / o1, "draw": 1.0 / oX, "away": 1.0 / o2}
        total = sum(raw.values())

        # 轻微平滑（5% 均匀分布），防止极端赔率导致的过自信
        uniform = 1.0 / 3.0
        result = {}
        for k in raw:
            prob = raw[k] / total
            result[k] = 0.95 * prob + 0.05 * uniform

        # 再归一化
        t2 = sum(result.values())
        return {k: v / t2 for k, v in result.items()}


# ────────────────────────────
# 模型 E：平局检测修正
# ────────────────────────────
class DrawDetectionModel:
    """平局检测子模型 — 优先使用NN分类器，fallback到规则式校准。

    核心问题: 当前融合SPF仅0.7%预测平局，实际占25%。
    规则式draw_calibrator已在walk-forward验证中失败(2026-05-12)。
    新方案: 训练专用二分类MLP(DrawClassifierNet)，用P(draw|features)修正SPF。
    当NN模型不可用时，fallback到draw_calibrator规则方案。
    """
    _nn_predictor: Optional[Any] = None

    @classmethod
    def _get_nn_predictor(cls) -> Optional[Any]:
        if cls._nn_predictor is None:
            try:
                from draw_classifier import DrawClassifierPredictor
                predictor = DrawClassifierPredictor()
                if predictor.is_ready():
                    cls._nn_predictor = predictor
            except Exception:
                pass
        return cls._nn_predictor

    @classmethod
    def predict(
        cls,
        spf: Dict[str, float],
        ctx: MatchContext,
        market: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """检测平局并修正SPF概率"""
        nn = cls._get_nn_predictor()
        if nn is not None:
            home_xg = getattr(ctx.home_team, "avg_xg", 0) or ctx.home_team.avg_goals_scored
            away_xg = getattr(ctx.away_team, "avg_xg", 0) or ctx.away_team.avg_goals_scored

            draw_prob_nn = nn.predict_from_match(
                elo_diff=ctx.home_team.elo - ctx.away_team.elo,
                xg_diff=home_xg - away_xg,
                market_draw_prob=market.get("draw") if market else None,
                model_draw_prob=spf.get("draw", 0.25),
                competition="",
                venue_type=getattr(ctx, "venue_type", "neutral"),
                temperature=getattr(ctx, "temperature", 20.0),
                odds_home=ctx.closing_odds_home or ctx.odds_home,
                odds_draw=ctx.closing_odds_draw or ctx.odds_draw,
                odds_away=ctx.closing_odds_away or ctx.odds_away,
                draw_movement=0.0,
            )
            return nn.adjust_spf(spf, draw_prob_nn)

        # Fallback: 规则式draw_calibrator (已验证效果差, 仅作兜底)
        from draw_calibrator import (
            DrawFeatures,
            apply_draw_calibration,
            load_draw_params,
        )
        home_xg = getattr(ctx.home_team, "avg_xg", 0) or ctx.home_team.avg_goals_scored
        away_xg = getattr(ctx.away_team, "avg_xg", 0) or ctx.away_team.avg_goals_scored
        features = DrawFeatures(
            elo_diff=ctx.home_team.elo - ctx.away_team.elo,
            xg_diff=home_xg - away_xg,
            market_draw_prob=market.get("draw") if market else None,
            is_knockout=ctx.is_knockout,
        )
        return apply_draw_calibration(spf, features, load_draw_params())


# ────────────────────────────
# 融合层
# ────────────────────────────
class EnsembleFusion:
    """线性加权融合，支持从历史数据学习动态权重"""

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self._db = db_session

    @staticmethod
    def _elo_diff_tier(elo_home: int, elo_away: int) -> str:
        diff = abs(elo_home - elo_away)
        if diff < 100:
            return "0-100"
        elif diff < 200:
            return "100-200"
        elif diff < 400:
            return "200-400"
        return "400+"

    @staticmethod
    def _stage_category(stage: str) -> str:
        """将具体阶段映射为 group / knockout / all"""
        if stage in ("group",):
            return "group"
        if stage in ("R32", "R16", "QF", "SF", "F", "3P", "knockout"):
            return "knockout"
        return "all"

    def _load_learned_weights(
        self, stage: str, elo_home: int, elo_away: int
    ) -> Optional[Dict[str, float]]:
        """从数据库加载最优权重，按 stage类别 + elo_diff 匹配"""
        if self._db is None:
            return None
        try:
            from models import FusionWeight

            elo_tier = self._elo_diff_tier(elo_home, elo_away)
            stage_cat = self._stage_category(stage)
            # 先尝试 stage类别 + elo_tier
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == stage_cat,
                    FusionWeight.elo_diff_range == elo_tier,
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 降级：只匹配 stage类别
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == stage_cat,
                    FusionWeight.elo_diff_range == "all",
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 降级：跨 stage 匹配 elo_tier
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == "all",
                    FusionWeight.elo_diff_range == elo_tier,
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 最终降级：全局权重
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == "all",
                    FusionWeight.elo_diff_range == "all",
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
        except Exception as e:
            logger.warning(f"[fusion] Failed to load learned weights, using defaults: {e}")
        return None

    @staticmethod
    def _parse_weights(raw) -> Dict[str, float]:
        """解析权重，兼容 JSON 字符串和 dict"""
        if isinstance(raw, dict):
            return {k: float(v) for k, v in raw.items()}
        if isinstance(raw, str):
            import json as _json
            return {k: float(v) for k, v in _json.loads(raw).items()}
        return DEFAULT_WEIGHTS.copy()

    def get_weights(self, ctx: MatchContext) -> Dict[str, float]:
        """获取融合权重：优先学习权重 → 传入权重 → 默认权重"""
        if self._db is not None:
            learned = self._load_learned_weights(
                ctx.stage, ctx.home_team.elo, ctx.away_team.elo
            )
            if learned:
                return learned
        return self.weights.copy()

    def get_effective_weights(
        self, market: Optional[Dict[str, float]], ctx: Optional[MatchContext] = None
    ) -> Dict[str, float]:
        """获取实际使用的融合权重（含无赔率降级后的重新分配）"""
        w = self.get_weights(ctx) if ctx else self.weights.copy()
        if market is None:
            total = w["elo"] + w["poisson"] + w["players"]
            if total > 0:
                w = {
                    "elo": w["elo"] / total,
                    "poisson": w["poisson"] / total,
                    "players": w["players"] / total,
                    "market": 0.0,
                }
        return w


    def fuse_spf(
        self,
        elo: Dict[str, float],
        poisson: Dict[str, float],
        players: float,
        market: Optional[Dict[str, float]],
        ctx: Optional[MatchContext] = None,
    ) -> Dict[str, float]:
        """
        融合胜平负概率。
        players 是战力修正系数，其权重控制修正强度（权重越高，
        players_factor 对 elo/poisson 的缩放越强）。
        ctx 可选，用于动态加载学习权重。
        """
        w = self.get_weights(ctx) if ctx else self.weights.copy()

        # 动态权重调整：有真实竞彩/收盘赔率时提升 market 权重
        if market is not None and ctx is not None:
            has_real_odds = ctx.has_closing_odds or (ctx.odds_home and ctx.odds_home > 1.01)
            is_league = ctx.stage in ("group", "") and not ctx.is_knockout
            if has_real_odds and is_league:
                # 联赛有真实赔率：market 权重提升到 50%
                boost = 0.50 - w.get("market", 0)
                if boost > 0:
                    w["market"] = 0.50
                    # 从 elo 和 poisson 平均扣除
                    w["elo"] = max(w.get("elo", 0) - boost * 0.4, 0.05)
                    w["poisson"] = max(w.get("poisson", 0) - boost * 0.4, 0.10)
                    w["players"] = max(w.get("players", 0) - boost * 0.2, 0.05)
                    total_w = sum(w.values())
                    w = {k: v / total_w for k, v in w.items()}

        if market is None:
            # 没有市场赔率时，权重在 elo/poisson/players 之间重新分配
            total = w["elo"] + w["poisson"] + w["players"]
            w = {
                "elo": w["elo"] / total,
                "poisson": w["poisson"] / total,
                "players": w["players"] / total,
                "market": 0.0,
            }

        # players 权重控制 adjust 强度：权重越高，players_factor 修正越强
        # 当 players 权重为 0 时，blend_factor=1.0（无调整）；权重为 0.3 时，接近全幅度调整
        adjust_strength = min(1.0, w["players"] * 3.0)
        blend_factor = 1.0 + (players - 1.0) * adjust_strength

        def adjust(probs: Dict[str, float], factor: float) -> Dict[str, float]:
            # factor > 1 增强主队，factor < 1 增强客队
            home_adj = probs["home"] * factor
            away_adj = probs["away"] / factor if factor > 0 else probs["away"]
            draw_adj = probs["draw"]
            t = home_adj + draw_adj + away_adj
            return {"home": home_adj / t, "draw": draw_adj / t, "away": away_adj / t}

        elo_adj = adjust(elo, blend_factor)
        poisson_adj = adjust(poisson, blend_factor)

        result = {}
        for outcome in ["home", "draw", "away"]:
            val = (
                w["elo"] * elo_adj[outcome]
                + w["poisson"] * poisson_adj[outcome]
                + w["market"] * (market.get(outcome, 1 / 3.0) if market else 0)
            )
            result[outcome] = val

        # 归一化
        total = sum(result.values())
        return {k: max(0.001, v / total) for k, v in result.items()}

    @classmethod
    def fuse_probabilities(
        cls,
        base: Dict[str, float],
        modifier: Dict[str, float],
        alpha: float = 0.7
    ) -> Dict[str, float]:
        """通用概率融合：base 为基础，modifier 为修正"""
        result = {}
        keys = set(base.keys()) | set(modifier.keys())
        for k in keys:
            b = base.get(k, 0)
            m = modifier.get(k, 0)
            result[k] = alpha * b + (1 - alpha) * m
        total = sum(result.values())
        return {k: max(0, v / total) for k, v in result.items()}


# ────────────────────────────
# 主预测引擎
# ────────────────────────────
class PredictionEngine:
    """
    整合全部子模型，对外提供统一的 predict() 接口。
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None,
                 use_lr_fusion: bool = True):
        self.fusion = EnsembleFusion(weights, db_session=db_session)
        self.use_lr_fusion = use_lr_fusion
        self._lr_weights_cache: Dict[str, LogisticFusionWeights] = {}
        self._feature_builder = FeatureBuilder(use_interactions=True)
        if use_lr_fusion:
            # 预加载全局权重作为默认值
            global_w = self._load_lr_weights("global")
            if global_w:
                self._lr_weights_cache["global"] = global_w

    @staticmethod
    def _load_lr_weights(league: str = "global") -> Optional["LogisticFusionWeights"]:
        """加载指定联赛的最新的 LR 融合权重，fallback 为 None"""
        import glob
        import os
        try:
            _lr_weights_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "weights", "lr")
            # 匹配模式: {league}_v1_*.json
            pattern = os.path.join(_lr_weights_dir, f"{league}_v1_*.json")
            lr_files = sorted(glob.glob(pattern))
            if lr_files:
                w = LogisticFusionWeights.load(lr_files[-1])
                import logging
                logging.getLogger("prediction_engine").info(
                    f"[LR-fusion] Loaded {league} weights: {os.path.basename(lr_files[-1])} "
                    f"(acc={w.accuracy:.1%}, n={w.sample_count})"
                )
                return w
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(
                f"[LR-fusion] Failed to load {league} weights: {e}"
            )
        return None

    def _get_lr_weights_for_match(self, competition: str) -> Optional["LogisticFusionWeights"]:
        """动态路由：根据比赛所属联赛选择最优权重"""
        # 1. 尝试从缓存中获取
        if competition in self._lr_weights_cache:
            return self._lr_weights_cache[competition]

        # 2. 尝试从磁盘加载该联赛专属权重
        w = self._load_lr_weights(competition)
        if w:
            self._lr_weights_cache[competition] = w
            return w

        # 3. Fallback 到全局权重
        return self._lr_weights_cache.get("global")

    def _apply_live_odds_override(

        self, spf: Dict[str, float], ctx: MatchContext
    ) -> Dict[str, float]:
        """临场跳水/异常赔率监控：当市场发生剧烈波动时，强制修正预测概率"""
        if not ctx.has_closing_odds or not ctx.has_odds:
            return spf

        # 1. 检测主队赔率波动
        move_h = (ctx.closing_odds_home - ctx.odds_home) / ctx.odds_home
        
        # 如果赔率大幅下降（即市场极其看好该项）
        if move_h < -0.12:  # 跳水超过 12%
            import logging
            logging.getLogger("prediction_engine").warning(
                f"[Override] Steam Move detected for match {ctx.match_id}: home odds dropped {abs(move_h):.1%}"
            )
            # 强化该项概率：50% 遵循原预测，50% 遵循市场最强信号
            spf["home"] = spf["home"] * 0.7 + 0.3 # 暴力补强
            total = sum(spf.values())
            spf = {k: v / total for k, v in spf.items()}
            
        return spf

    def predict(self, ctx: MatchContext) -> PredictionResult:
        from core.logic_tracer import LogicChain
        trace = LogicChain(match_id=ctx.match_id)

        # 1. 跑各子模型
        elo_out = EloModel.predict(ctx)
        poisson_out = PoissonModel.predict(ctx)
        players_factor = PlayerAdjustmentModel.predict(ctx)
        market_out = MarketModel.predict(ctx)

        # 2. 融合胜平负 ── 优先使用 LR 逻辑回归融合 (v2)
        lr_spf = None
        weights = self._get_lr_weights_for_match(ctx.competition)
        if weights:
            lr_spf = self._predict_with_lr(
                ctx, elo_out, poisson_out, players_factor, market_out, weights
            )

        if lr_spf is not None:
            fused_spf = lr_spf
            trace.add_step("逻辑回归基准", f"使用 {ctx.competition or '全球'} 48维特征模型计算出的初始概率分布", fused_spf)
            # 💡 核心改进：混合市场信号 (50/50) 以提升基准准确率
            if market_out:
                fused_spf = {
                    k: 0.5 * fused_spf[k] + 0.5 * market_out[k]
                    for k in ["home", "draw", "away"]
                }
                trace.add_step("市场共识校准", "将模型预测与机构赔率隐含概率按 50:50 融合，平滑非理性波动", fused_spf)
        else:
            # fallback: 旧4参数线性加权 (EnsembleFusion)
            fused_spf = self.fusion.fuse_spf(
                elo=elo_out,
                poisson=poisson_out["spf"],
                players=players_factor,
                market=market_out,
                ctx=ctx,
            )
            trace.add_step("线性加权基准", "由于缺少专属权重，使用基础 Elo+泊松 4 参数融合", fused_spf)

        # 2b. 临场跳水修正 (New!)
        old_spf = fused_spf.copy()
        fused_spf = self._apply_live_odds_override(fused_spf, ctx)
        if fused_spf != old_spf:
            trace.add_step("临场异动修正", "检测到赔率剧烈跳水（Steam Move），强制对齐机构大额资金流向", fused_spf)

        # 2c. 平局检测修正：精细化校准
        fused_spf = DrawDetectionModel.predict(fused_spf, ctx, market_out)
        trace.add_step("平局概率微调", "利用 Draw-MLP 分类器针对高相关性特征进行平局偏置修正", fused_spf)

        # 2d. 残差 NN 修正：用 ResidualNet 修正 LR 系统性偏差
        if lr_spf is not None:
            fused_spf = self._apply_residual_correction(fused_spf, ctx, poisson_out, market_out)
            trace.add_step("利润导向 NN 修正", "神经网络通过残差学习捕捉市场错价空间，优化最终 ROI 期望", fused_spf)

        # 3. 让球：基于泊松输出，但用融合后的 spf 做最终归一化参考
        rq_raw = poisson_out["rq"].copy()
        # 让球概率的方向应与融合spf一致
        spf_direction = fused_spf["home"] - fused_spf["away"]
        rq_direction = rq_raw["home"] - rq_raw["away"]
        if spf_direction * rq_direction < 0:
            # 方向相反，取平均（保守处理）
            rq_raw["home"] = (rq_raw["home"] + fused_spf["home"]) / 2
            rq_raw["away"] = (rq_raw["away"] + fused_spf["away"]) / 2
            rq_raw["draw"] = 1 - rq_raw["home"] - rq_raw["away"]
        rq = {k: max(0.001, v) for k, v in rq_raw.items() if k != "handicap"}
        total = sum(rq.values())
        rq = {k: v / total for k, v in rq.items()}

        # 4. 比分 / 总进球 / 半全场：直接取泊松输出（这些玩法的概率结构由泊松天然生成）
        score = poisson_out["score"]
        goals = poisson_out["goals"]
        half = poisson_out["half"]

        # 5. 置信度判断
        confidence = self._compute_confidence(fused_spf, market_out, ctx)

        return PredictionResult(
            match_id=ctx.match_id,
            spf=fused_spf,
            rq=rq,
            score=score,
            goals=goals,
            half=half,
            raw_elo=elo_out,
            raw_poisson=poisson_out["spf"],
            raw_players=players_factor,
            raw_market=market_out or {},
            model_version="v2.0-lr" if lr_spf is not None else "v1.0",
            confidence=confidence,
            odds_degraded=market_out is None,
            weights_used={"_fusion": "lr_v2", **(lr_spf or {})} if lr_spf is not None else self.fusion.get_effective_weights(market_out, ctx),
            trace=trace,
        )

    def _predict_with_lr(self, ctx: MatchContext, elo_out: Dict[str, float], poisson_out: Dict, players_factor: float, market_out: Optional[Dict[str, float]], weights: "LogisticFusionWeights",) -> Optional[Dict[str, float]]:
        """使用 LR 逻辑回归融合预测 SPF。失败时返回 None，fallback 到旧融合。"""
        try:
            # 构建 FormMarkov + H2H 特征（需要 DB session）
            form_features = None
            h2h_features = None
            if self.fusion._db is not None:
                try:
                    fm = FormMarkovModel(self.fusion._db)
                    form_features = fm.compute(
                        ctx.home_team.recent_results,
                        ctx.home_team.team_id,
                        is_home=True,
                    )
                    hm = H2HModel(self.fusion._db)
                    h2h_features = hm.compute(
                        ctx.home_team.team_id,
                        ctx.away_team.team_id,
                    )
                except Exception:
                    pass  # Form/H2H 不可用时 FeatureBuilder 会填充默认值

            # 构建 43 维特征向量
            features = self._feature_builder.build(
                elo_probs=elo_out,
                poisson_result=poisson_out,
                players_factor=players_factor,
                market_probs=market_out,
                form_features=form_features,
                h2h_features=h2h_features,
                ctx=ctx,
            )

            # LR 推理
            lr_probs = weights.predict(features)
            return lr_probs

        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(
                f"[LR-fusion] predict failed, fallback to EnsembleFusion: {e}"
            )
            return None

    def _apply_residual_correction(
        self,
        spf: Dict[str, float],
        ctx: MatchContext,
        poisson_out: Dict,
        market_out: Optional[Dict[str, float]],
    ) -> Dict[str, float]:
        """使用残差 NN 修正 LR 融合的系统性偏差。NN 不可用时返回原值。"""
        try:
            from residual_nn import ResidualPredictor
            predictor = ResidualPredictor()
            if not predictor.is_ready():
                return spf

            # 构建 residual NN 所需的输入
            odds = {
                "home": ctx.odds_home or ctx.closing_odds_home or 2.0,
                "draw": ctx.odds_draw or ctx.closing_odds_draw or 3.0,
                "away": ctx.odds_away or ctx.closing_odds_away or 2.0,
            }
            odds_movement = {}
            for sel in ["home", "draw", "away"]:
                c = getattr(ctx, f"closing_odds_{sel}", None) or 0
                o = getattr(ctx, f"opening_odds_{sel}", None) or 0
                odds_movement[sel] = (c - o) / o if c and o else 0.0

            delta = predictor.predict_delta(
                lr_probs=spf,
                spf_probs=spf,
                rq_probs=poisson_out.get("rq", spf),
                score_top3=poisson_out.get("score", {}),
                odds=odds,
                elo_diff=0.0,  # 训练时用的0，推理必须一致
                odds_movement=odds_movement,
                competition="",  # 训练时用的空串，推理必须一致
            )

            corrected = ResidualPredictor.apply_correction(spf, delta, alpha=0.3)

            import logging
            logging.getLogger("prediction_engine").debug(
                f"[ResidualNN] correction applied: "
                f"H {spf['home']:.3f}->{corrected['home']:.3f} "
                f"D {spf['draw']:.3f}->{corrected['draw']:.3f} "
                f"A {spf['away']:.3f}->{corrected['away']:.3f}"
            )
            return corrected

        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").debug(
                f"[ResidualNN] correction skipped: {e}"
            )
            return spf

    @staticmethod
    def _compute_confidence(
        spf: Dict[str, float],
        market: Optional[Dict[str, float]],
        ctx: MatchContext,
    ) -> str:
        """
        置信度分级：
        high — 模型与市场高度一致，且某一方概率 > 60%
        medium — 模型与市场基本一致，或概率接近
        low — 模型与市场分歧大，或存在异常信号

        无赔率时自动降级：缺少市场信号（权重 0.64）的预测
        可靠性显著下降，high → medium，medium → low。
        """
        max_prob = max(spf.values())
        has_market = market is not None and len(market) > 0

        # 无赔率降级：缺少最强信号源，置信度上限降低
        if not has_market:
            if max_prob >= 0.65:
                return "medium"
            return "low"

        # 分歧检测
        disagreement = sum(abs(spf[k] - market.get(k, 0)) for k in spf) / 2
        if disagreement > 0.12:
            return "low"
        if disagreement > 0.06:
            return "medium"

        # 概率集中度
        if max_prob >= 0.60:
            return "high"
        if max_prob >= 0.45:
            return "medium"
        return "low"


# ────────────────────────────
# 回测框架
# ────────────────────────────
@dataclass
class BacktestResult:
    """回测结果"""
    total_matches: int
    direction_accuracy: float          # 方向准确率（猜对胜平负）
    high_conf_accuracy: float          # 高置信度准确率
    brier_score: float                 # 概率校准度（越低越好）
    log_loss: float                    # 对数损失
    avg_max_prob: float                # 平均最高概率（反映模型自信度）
    weights: Dict[str, float]          # 最优权重


def brier_score(prob_true: float, outcome: int) -> float:
    """Brier Score: (prob - outcome)^2, outcome ∈ {0,1}"""
    return (prob_true - outcome) ** 2


def direction_correct(pred: Dict[str, float], actual: str) -> bool:
    """预测方向是否正确"""
    predicted = max(pred, key=pred.get)
    return predicted == actual


class Backtester:
    """
    历史回测：遍历历史比赛，用不同权重跑预测，评估指标。
    """

    def __init__(self, engine: PredictionEngine):
        self.engine = engine

    def evaluate_single(
        self,
        ctx: MatchContext,
        actual_outcome: str,   # "home" / "draw" / "away"
    ) -> Dict[str, float]:
        """评估单场比赛"""
        result = self.engine.predict(ctx)
        spf = result.spf

        # 方向
        correct = direction_correct(spf, actual_outcome)

        # Brier Score（对3个结果分别计算，取平均）
        bs = sum(
            brier_score(spf[k], 1 if actual_outcome == k else 0)
            for k in ["home", "draw", "away"]
        ) / 3.0

        # Log Loss（加平滑避免log(0)）
        prob = spf.get(actual_outcome, 1e-6)
        ll = -math.log(max(prob, 1e-6))

        return {
            "correct": float(correct),
            "brier": bs,
            "log_loss": ll,
            "max_prob": max(spf.values()),
            "confidence": result.confidence,
        }

    def run(
        self,
        historical_matches: List[Tuple[MatchContext, str]],
        weight_grids: Optional[Dict[str, List[float]]] = None,
    ) -> BacktestResult:
        """
        对历史数据跑回测，并网格搜索最优权重。

        historical_matches: [(MatchContext, actual_outcome), ...]
        """
        if weight_grids is None:
            # 默认网格：elo 0.1~0.5, poisson 0.2~0.6, players固定0.15, market 0~0.4
            weight_grids = {
                "elo": [0.1, 0.2, 0.3, 0.4, 0.5],
                "poisson": [0.2, 0.3, 0.4, 0.5, 0.6],
                "market": [0.0, 0.1, 0.2, 0.3, 0.4],
            }

        best_score = float("inf")
        best_weights = DEFAULT_WEIGHTS.copy()

        # 网格搜索（players 权重由剩余决定）
        for w_elo in weight_grids["elo"]:
            for w_poisson in weight_grids["poisson"]:
                for w_market in weight_grids["market"]:
                    w_players = 1.0 - w_elo - w_poisson - w_market
                    if w_players < 0 or w_players > 0.5:
                        continue

                    weights = {
                        "elo": w_elo,
                        "poisson": w_poisson,
                        "players": w_players,
                        "market": w_market,
                    }

                    # 跑回测
                    metrics = self._evaluate_weights(historical_matches, weights)
                    # 目标：最小化 Brier Score
                    score = metrics["avg_brier"]

                    if score < best_score:
                        best_score = score
                        best_weights = weights.copy()
                        best_metrics = metrics

        return BacktestResult(
            total_matches=len(historical_matches),
            direction_accuracy=best_metrics["accuracy"],
            high_conf_accuracy=best_metrics["high_conf_accuracy"],
            brier_score=best_metrics["avg_brier"],
            log_loss=best_metrics["avg_log_loss"],
            avg_max_prob=best_metrics["avg_max_prob"],
            weights=best_weights,
        )

    def _evaluate_weights(
        self,
        matches: List[Tuple[MatchContext, str]],
        weights: Dict[str, float],
    ) -> Dict[str, float]:
        """用给定权重跑全部历史比赛"""
        engine = PredictionEngine(weights=weights)
        results = []

        for ctx, actual in matches:
            try:
                r = engine.predict(ctx)
                spf = r.spf

                correct = direction_correct(spf, actual)
                bs = sum(
                    brier_score(spf[k], 1 if actual == k else 0)
                    for k in ["home", "draw", "away"]
                ) / 3.0
                ll = -math.log(max(spf.get(actual, 1e-6), 1e-6))

                results.append({
                    "correct": correct,
                    "brier": bs,
                    "log_loss": ll,
                    "max_prob": max(spf.values()),
                    "confidence": r.confidence,
                })
            except Exception as e:
                # 单场比赛失败不影响整体
                continue

        if not results:
            return {"accuracy": 0, "avg_brier": 1.0, "avg_log_loss": 10, "avg_max_prob": 0, "high_conf_accuracy": 0}

        corrects = [r["correct"] for r in results]
        briers = [r["brier"] for r in results]
        log_losses = [r["log_loss"] for r in results]
        max_probs = [r["max_prob"] for r in results]

        high_conf = [r for r in results if r["confidence"] == "high"]

        return {
            "accuracy": sum(corrects) / len(corrects),
            "avg_brier": sum(briers) / len(briers),
            "avg_log_loss": sum(log_losses) / len(log_losses),
            "avg_max_prob": sum(max_probs) / len(max_probs),
            "high_conf_accuracy": sum(r["correct"] for r in high_conf) / len(high_conf) if high_conf else 0,
        }


# ────────────────────────────
# ORM → TeamContext / MatchContext 构建
# ────────────────────────────
def build_team_context_from_orm(team) -> TeamContext:
    """从数据库 Team ORM 对象构建 TeamContext（供调度器/回测/seed 统一使用）。"""
    # possession → 战术风格推断校准
    tactical = team.tactical_style or "balanced"
    if team.possession and team.possession > 55 and tactical == "balanced":
        tactical = "attack"
    elif team.possession and team.possession < 45 and tactical == "balanced":
        tactical = "counter"

    return TeamContext(
        team_id=team.id,
        name=team.name,
        elo=team.elo or 1500,
        fifa_rank=team.fifa_rank or 100,
        avg_goals_scored=team.avg_goals_scored or 1.3,
        avg_goals_conceded=team.avg_goals_conceded or 1.3,
        avg_xg=team.avg_xg or 0.0,
        avg_xga=team.avg_xga or 0.0,
        possession=team.possession or 0.0,
        pass_completion=team.pass_completion or 0.0,
        shots_per_game=team.shots_per_game or 0.0,
        form_factor=team.form_factor or 1.0,
        recent_results=team.recent_results or "",
        recent_goals_scored=team.recent_goals_scored or 0.0,
        recent_goals_conceded=team.recent_goals_conceded or 0.0,
        home_away_factor=team.home_away_factor or 1.0,
        weather_adaptability=team.weather_adaptability or 1.0,
        tactical_style=tactical,
        coach_rating=team.coach_rating or 0.5,
        rest_days=team.rest_days or 7,
        key_injuries=team.key_injuries or "",
        squad_fatigue_index=team.squad_fatigue_index or 0.5,
    )


def build_context_from_match(match, handicap: int = 0) -> MatchContext:
    """从数据库 Match ORM 对象构建完整的 MatchContext。"""
    home = build_team_context_from_orm(match.home_team)
    away = build_team_context_from_orm(match.away_team)
    # 判定是否为赛季冲刺阶段 (5月-6月)
    is_late = False
    if match.kickoff_at:
        is_late = match.kickoff_at.month in (5, 6)

    return MatchContext(
        match_id=match.id,
        home_team=home,
        away_team=away,
        stage=match.stage or "group",
        is_knockout=match.stage in ("R32", "R16", "QF", "SF", "F"),
        is_late_season=is_late,
        handicap=handicap,
        odds_home=match.odds_home,
        odds_draw=match.odds_draw,
        odds_away=match.odds_away,
        closing_odds_home=match.closing_odds_home,
        closing_odds_draw=match.closing_odds_draw,
        closing_odds_away=match.closing_odds_away,
        venue_type=match.venue_type or "neutral",
        weather=match.weather or "clear",
        temperature=match.temperature or 20.0,
        pitch_condition=match.pitch_condition or "good",
        schedule_density=match.schedule_density or "normal",
        competition=match.competition or "",
    )




# ────────────────────────────
# Mock 数据生成（用于测试）
# ────────────────────────────
def create_mock_context(
    match_id: int = 1,
    home_elo: int = 1985,
    away_elo: int = 1920,
    home_rank: int = 1,
    away_rank: int = 5,
    odds_home: float = 1.72,
    odds_draw: float = 3.40,
    odds_away: float = 4.80,
    stage: str = "group",
    is_knockout: bool = False,
) -> MatchContext:
    """生成测试用的 Mock 比赛上下文"""
    home = TeamContext(
        team_id=1,
        name="阿根廷",
        elo=home_elo,
        fifa_rank=home_rank,
        avg_goals_scored=1.80,
        avg_goals_conceded=0.70,
        form_factor=1.10,
        key_players_available=11,
        squad_fatigue_index=0.30,
    )
    away = TeamContext(
        team_id=2,
        name="巴西",
        elo=away_elo,
        fifa_rank=away_rank,
        avg_goals_scored=1.60,
        avg_goals_conceded=0.90,
        form_factor=1.05,
        key_players_available=10,
        squad_fatigue_index=0.40,
    )
    return MatchContext(
        match_id=match_id,
        home_team=home,
        away_team=away,
        stage=stage,
        is_knockout=is_knockout,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
    )


# ────────────────────────────
# 投注策略引擎
# ────────────────────────────

@dataclass
class StrategyPick:
    """单个投注推荐"""
    strategy_name: str           # 策略名称
    strategy_type: str           # kelly / conservative / probability / ev_max / combo
    play_type: str               # spf / rq / score / goals / half
    play_label: str              # 玩法中文名
    selection: str               # 选项
    selection_label: str         # 选项中文名
    probability: float           # 模型概率
    odds: float                  # 赔率
    ev: float                    # 期望值 (prob * odds - 1)
    kelly_fraction: float        # 凯利比例
    stake_pct: float             # 建议投注比例 (%)
    confidence: str              # high / medium / low
    rationale: str               # 推荐理由
    risk_level: str              # low / medium / high


class BettingStrategy:
    """
    基于预测结果生成多维度投注策略。

    策略体系:
      1. 凯利准则 (kelly)      — 利益最大化，计算最优投注比例
      2. 保守策略 (conservative) — 风险最小，只选高置信+正EV
      3. 概率优先 (probability)  — 准确率最高，选概率最大
      4. EV最大化 (ev_max)       — 期望收益最高，选EV最大
      5. 组合策略 (combo)        — 综合评分最高，平衡收益与风险
    """

    # 玩法配置
    PLAY_CONFIG = {
        "SPF": {
            "label": "胜平负",
            "options": [
                ("home", "主胜"),
                ("draw", "平"),
                ("away", "客胜"),
            ],
            "odds_keys": ["odds_home", "odds_draw", "odds_away"],
        },
        "RQ": {
            "label": "让球",
            "options": [
                ("home", "让胜"),
                ("draw", "让平"),
                ("away", "让负"),
            ],
            "odds_keys": ["odds_home", "odds_draw", "odds_away"],
        },
        "SCORE": {
            "label": "比分",
            "options": None,  # 动态从概率中取 Top
            "odds_keys": [],
        },
        "GOALS": {
            "label": "总进球",
            "options": None,  # 动态
            "odds_keys": [],
        },
        "HALF": {
            "label": "半全场",
            "options": [
                ("home_home", "主主"),
                ("home_draw", "主平"),
                ("home_away", "主客"),
                ("draw_home", "平主"),
                ("draw_draw", "平平"),
                ("draw_away", "平客"),
                ("away_home", "客主"),
                ("away_draw", "客平"),
                ("away_away", "客客"),
            ],
            "odds_keys": [],
        },
    }

    def __init__(self, bankroll: float = 100.0, max_kelly: float = 0.25):
        """
        bankroll: 总资金（用于计算建议投注金额）
        max_kelly: 凯利比例上限（默认 25%，防止过度投注）
        """
        self.bankroll = bankroll
        self.max_kelly = max_kelly

    # ─── 工具方法 ───

    @staticmethod
    def calc_ev(prob: float, odds: float) -> float:
        return prob * odds - 1.0

    @staticmethod
    def calc_kelly(prob: float, odds: float) -> float:
        """凯利公式: f = (p*o - 1) / (o - 1)"""
        if odds <= 1.0:
            return 0.0
        k = (prob * odds - 1.0) / (odds - 1.0)
        return max(0.0, k)

    @staticmethod
    def score_to_odds(score_key: str) -> float:
        """比分赔率映射（简化模型，实际应由赔率采集提供）"""
        # 常见比分赔率参考（基于竞彩历史数据）
        odds_map = {
            "0:0": 8.0, "1:0": 6.0, "0:1": 7.0,
            "1:1": 6.5, "2:0": 7.5, "0:2": 9.0,
            "2:1": 7.5, "1:2": 8.5, "2:2": 13.0,
            "3:0": 11.0, "0:3": 16.0, "3:1": 10.0,
            "1:3": 13.0, "3:2": 14.0, "2:3": 16.0,
            "4:0": 18.0, "0:4": 28.0, "4:1": 15.0,
            "1:4": 22.0, "4:2": 22.0, "2:4": 28.0,
            "4:3": 35.0, "3:4": 45.0, "5:0": 35.0,
            "0:5": 60.0, "5:1": 30.0, "1:5": 50.0,
        }
        return odds_map.get(score_key, 25.0)

    @staticmethod
    def goals_to_odds(goals_key: str) -> float:
        """总进球赔率映射"""
        odds_map = {
            "0": 9.0, "1": 5.5, "2": 3.8, "3": 3.6,
            "4": 4.5, "5": 6.5, "6": 10.0, "7+": 12.0,
        }
        return odds_map.get(str(goals_key), 8.0)

    @staticmethod
    def half_to_odds(half_key: str) -> float:
        """半全场赔率映射"""
        odds_map = {
            "home_home": 3.0, "home_draw": 13.0, "home_away": 35.0,
            "draw_home": 5.0, "draw_draw": 5.5, "draw_away": 11.0,
            "away_home": 25.0, "away_draw": 13.0, "away_away": 4.0,
        }
        return odds_map.get(half_key, 15.0)

    def _get_odds(self, play_type: str, selection: str, match_odds: Dict[str, float]) -> float:
        """获取指定玩法的赔率"""
        if play_type == "SPF":
            return match_odds.get("odds_home", 2.0) if selection == "home" else \
                   match_odds.get("odds_draw", 3.2) if selection == "draw" else \
                   match_odds.get("odds_away", 3.5)
        elif play_type == "RQ":
            return match_odds.get("odds_home", 2.0) if selection == "home" else \
                   match_odds.get("odds_draw", 3.2) if selection == "draw" else \
                   match_odds.get("odds_away", 3.5)
        elif play_type == "SCORE":
            return self.score_to_odds(selection)
        elif play_type == "GOALS":
            return self.goals_to_odds(selection)
        elif play_type == "HALF":
            return self.half_to_odds(selection)
        return 2.0

    def _get_option_label(self, play_type: str, selection: str) -> str:
        """获取选项中文标签"""
        config = self.PLAY_CONFIG.get(play_type)
        if not config:
            return selection
        for key, label in (config["options"] or []):
            if key == selection:
                return label
        return selection

    def _confidence_score(self, confidence: str) -> float:
        return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(confidence, 0.5)

    def _risk_level(self, prob: float, ev: float, confidence: str) -> str:
        """评估风险等级"""
        if confidence == "high" and prob > 0.55 and ev > 0.05:
            return "low"
        if confidence == "low" or prob < 0.35 or ev < -0.1:
            return "high"
        return "medium"

    # ─── 核心策略 ───

    def _kelly_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """凯利准则：选凯利比例最高的正EV选项"""
        best = None
        best_kelly = 0.0
        for c in candidates:
            if c["ev"] <= 0:
                continue
            k = self.calc_kelly(c["prob"], c["odds"])
            if k > best_kelly:
                best_kelly = k
                best = c
        if not best:
            return None
        stake = min(best_kelly, self.max_kelly) * 100  # 转化为百分比
        return self._build_pick("kelly", "凯利准则", best, stake,
                                f"凯利比例 {best_kelly:.1%}，期望收益最高")

    def _conservative_strategy(self, candidates: List[Dict], overall_confidence: str) -> Optional[StrategyPick]:
        """保守策略：只选高置信 + 正EV + 概率>50%"""
        if overall_confidence != "high":
            return None
        best = None
        best_ev = -999
        for c in candidates:
            if c["ev"] <= 0 or c["prob"] < 0.50:
                continue
            if c["ev"] > best_ev:
                best_ev = c["ev"]
                best = c
        if not best:
            return None
        return self._build_pick("conservative", "保守策略", best, 5.0,
                                f"高置信+正EV，风险最低")

    def _probability_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """概率优先：选概率最大的"""
        best = max(candidates, key=lambda x: x["prob"])
        stake = min(10.0, max(2.0, best["prob"] * 15))  # 概率越高，投注越多
        return self._build_pick("probability", "概率优先", best, stake,
                                f"模型概率最高 {best['prob']:.1%}")

    def _ev_max_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """EV最大化：选期望收益最高的"""
        best = max(candidates, key=lambda x: x["ev"])
        if best["ev"] <= 0:
            # 如果没有正EV，选最接近0的
            best = max(candidates, key=lambda x: x["ev"])
        stake = min(12.0, max(2.0, best["ev"] * 30 + 5)) if best["ev"] > 0 else 2.0
        return self._build_pick("ev_max", "EV最大化", best, stake,
                                f"期望值 {'+' if best['ev']>0 else ''}{best['ev']:.1%}")

    def _combo_strategy(self, candidates: List[Dict], overall_confidence: str) -> Optional[StrategyPick]:
        """组合策略：综合评分 = 概率 * EV * 置信度权重"""
        conf_score = self._confidence_score(overall_confidence)
        best = None
        best_score = -999
        for c in candidates:
            # 综合评分: 概率 * max(EV, 0) * 置信度
            score = c["prob"] * max(c["ev"], 0) * conf_score
            if c["ev"] < 0:
                score = c["prob"] * (1 + c["ev"]) * conf_score * 0.5  # 负EV降权
            if score > best_score:
                best_score = score
                best = c
        if not best:
            return None
        stake = min(10.0, max(3.0, best["prob"] * best["odds"] * 3))
        ev_sign = "+" if best["ev"] > 0 else ""
        return self._build_pick("combo", "组合策略", best, stake,
                                f"综合评分最优，概率×EV平衡")

    def _build_pick(self, stype: str, sname: str, c: Dict, stake: float, rationale: str) -> StrategyPick:
        risk = self._risk_level(c["prob"], c["ev"], c.get("confidence", "medium"))
        return StrategyPick(
            strategy_name=sname,
            strategy_type=stype,
            play_type=c["play_type"],
            play_label=self.PLAY_CONFIG[c["play_type"]]["label"],
            selection=c["selection"],
            selection_label=self._get_option_label(c["play_type"], c["selection"]),
            probability=c["prob"],
            odds=c["odds"],
            ev=c["ev"],
            kelly_fraction=self.calc_kelly(c["prob"], c["odds"]),
            stake_pct=round(stake, 1),
            confidence=c.get("confidence", "medium"),
            rationale=rationale,
            risk_level=risk,
        )

    # ─── 主入口 ───

    def generate(
        self,
        predictions: List[Dict[str, Any]],
        match_odds: Dict[str, float],
        overall_confidence: str = "medium",
    ) -> List[StrategyPick]:
        """
        基于预测结果生成全部策略推荐。

        predictions: 从 Prediction 表读取的 [{play_type, probabilities}, ...]
        match_odds:  {odds_home, odds_draw, odds_away}
        """
        # 1. 构建候选池（每个玩法的每个选项）
        candidates = []
        for pred in predictions:
            ptype = pred.get("play_type")
            probs = pred.get("probabilities", {})
            config = self.PLAY_CONFIG.get(ptype)
            if not config:
                continue

            # 比分/总进球 特殊处理：取 Top 选项
            if ptype in ("SCORE", "GOALS"):
                top_items = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
                for sel, prob in top_items:
                    odds = self._get_odds(ptype, sel, match_odds)
                    candidates.append({
                        "play_type": ptype,
                        "selection": sel,
                        "prob": prob,
                        "odds": odds,
                        "ev": self.calc_ev(prob, odds),
                        "confidence": overall_confidence,
                    })
            else:
                for sel, label in (config["options"] or []):
                    prob = probs.get(sel, 0)
                    if prob <= 0:
                        continue
                    odds = self._get_odds(ptype, sel, match_odds)
                    candidates.append({
                        "play_type": ptype,
                        "selection": sel,
                        "prob": prob,
                        "odds": odds,
                        "ev": self.calc_ev(prob, odds),
                        "confidence": overall_confidence,
                    })

        if not candidates:
            return []

        # 2. 运行各策略
        results = []

        kelly = self._kelly_strategy(candidates)
        if kelly:
            results.append(kelly)

        conservative = self._conservative_strategy(candidates, overall_confidence)
        if conservative:
            results.append(conservative)

        prob = self._probability_strategy(candidates)
        if prob:
            results.append(prob)

        ev_max = self._ev_max_strategy(candidates)
        if ev_max:
            results.append(ev_max)

        combo = self._combo_strategy(candidates, overall_confidence)
        if combo:
            results.append(combo)

        return results


# ────────────────────────────
# CLI 测试入口
# ────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("世界杯预测引擎 — 测试运行")
    print("=" * 60)

    # 1. 单场比赛预测
    ctx = create_mock_context()
    engine = PredictionEngine()
    result = engine.predict(ctx)

    print("\n【阿根廷 vs 巴西】预测结果")
    print(f"置信度: {result.confidence.upper()}")
    print(f"权重: {result.weights_used}")
    print("\n--- 胜平负 ---")
    for k, v in result.spf.items():
        print(f"  {k}: {v:.2%}")

    print("\n--- 让球(-1) ---")
    for k, v in result.rq.items():
        print(f"  {k}: {v:.2%}")

    print("\n--- 比分 TOP 5 ---")
    for score, prob in sorted(result.score.items(), key=lambda x: -x[1])[:5]:
        print(f"  {score}: {prob:.2%}")

    print("\n--- 总进球 ---")
    for g, prob in sorted(result.goals.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)[:5]:
        print(f"  {g}球: {prob:.2%}")

    print("\n--- 半全场 TOP 5 ---")
    for h, prob in sorted(result.half.items(), key=lambda x: -x[1])[:5]:
        print(f"  {h}: {prob:.2%}")

    print("\n--- 模型拆解 ---")
    print(f"  Elo:       {result.raw_elo}")
    print(f"  Poisson:   {result.raw_poisson}")
    print(f"  Players:   {result.raw_players:.3f}")
    print(f"  Market:    {result.raw_market}")

    # 2. 简单回测演示（用模拟数据）
    print("\n" + "=" * 60)
    print("回测演示（模拟数据）")
    print("=" * 60)

    # 构造20场模拟历史比赛
    mock_history = []
    np.random.seed(42)
    for i in range(20):
        elo_diff = np.random.normal(50, 200)
        h_elo = 1800 + elo_diff / 2
        a_elo = 1800 - elo_diff / 2
        ctx = create_mock_context(
            match_id=i + 100,
            home_elo=int(h_elo),
            away_elo=int(a_elo),
            home_rank=max(1, int(50 - elo_diff / 40)),
            away_rank=max(1, int(50 + elo_diff / 40)),
            odds_home=2.0 - elo_diff / 400,
            odds_draw=3.2,
            odds_away=2.0 + elo_diff / 400,
        )
        # 模拟实际结果：Elo高的球队赢面大
        p_home = 1 / (1 + 10 ** (-(h_elo - a_elo) / 400))
        r = np.random.random()
        if r < p_home:
            actual = "home"
        elif r < p_home + 0.25:
            actual = "draw"
        else:
            actual = "away"
        mock_history.append((ctx, actual))

    backtester = Backtester(engine)
    bt_result = backtester.run(mock_history)

    print(f"\n回测结果（{bt_result.total_matches}场）")
    print(f"  方向准确率:       {bt_result.direction_accuracy:.2%}")
    print(f"  高置信度准确率:    {bt_result.high_conf_accuracy:.2%}")
    print(f"  Brier Score:      {bt_result.brier_score:.4f}  (越低越好, 随机=0.22)")
    print(f"  Log Loss:         {bt_result.log_loss:.4f}")
    print(f"  平均最高概率:      {bt_result.avg_max_prob:.2%}")
    print(f"  最优权重:         {bt_result.weights}")

    print("\n✅ 引擎测试完成")
