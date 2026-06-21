"""
数据上下文定义 — TeamContext, RefereeContext, MatchContext, PredictionResult

这些是预测引擎的基础数据结构,独立于模型逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from database.models import PlayType


# ────────────────────────────
# 数据结构
# ────────────────────────────

@dataclass
class TeamContext:
    """一支球队的赛前上下文"""
    team_id: int
    name: str
    name_en: str = ""
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
    key_injuries: str = ""

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
    kickoff_at: Optional[datetime] = None

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
    model_version: str = "v2.0"
    confidence: str = "medium"   # high / medium / low
    odds_degraded: bool = False  # True when prediction lacks market odds
    weights_used: Dict[str, Any] = field(default_factory=dict)
    trace: Optional[Any] = None  # LogicChain object
    shadow_data: Optional[Dict[str, Any]] = None  # v3.0 一致性混合对齐引擎
    classic_data: Optional[Dict[str, Any]] = None  # v3.0_classic 纯物理 Dixon-Coles 引擎
    deep_data: Optional[Dict[str, Any]] = None     # v4.0 深度学习时序 xG 物理融合引擎
    mixture_signals: Optional[Dict[str, Any]] = None  # 混合比分模型信号 (collapse_prob, upset)

    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_db_payload(self) -> List[Dict[str, Any]]:
        payload = [
            {"play_type": PlayType.SPF, "probabilities": self.spf, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.RQ, "probabilities": self.rq, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.SCORE, "probabilities": self.score, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.GOALS, "probabilities": self.goals, "confidence": self.confidence, "model_version": self.model_version},
            {"play_type": PlayType.HALF, "probabilities": self.half, "confidence": self.confidence, "model_version": self.model_version},
        ]

        play_type_map = {
            "spf": PlayType.SPF,
            "rq": PlayType.RQ,
            "score": PlayType.SCORE,
            "goals": PlayType.GOALS,
            "half": PlayType.HALF
        }

        if self.shadow_data:
            for play_key, probs in self.shadow_data.items():
                ptype = play_type_map.get(play_key)
                if ptype:
                     payload.append({
                         "play_type": ptype,
                         "probabilities": probs,
                         "confidence": self.confidence,
                         "model_version": "v3.0"
                     })

        if self.classic_data:
            for play_key, probs in self.classic_data.items():
                ptype = play_type_map.get(play_key)
                if ptype:
                     payload.append({
                         "play_type": ptype,
                         "probabilities": probs,
                         "confidence": self.confidence,
                         "model_version": "v3.0_classic"
                     })

        if self.deep_data:
            for play_key, probs in self.deep_data.items():
                ptype = play_type_map.get(play_key)
                if ptype:
                     payload.append({
                         "play_type": ptype,
                         "probabilities": probs,
                         "confidence": self.confidence,
                         "model_version": "v4.0"
                     })

        # 混合比分模型信号 — 存入 SPF 的 probabilities 中作为元数据
        if self.mixture_signals:
            payload.append({
                "play_type": PlayType.SPF,
                "probabilities": {
                    "_mixture": self.mixture_signals,
                },
                "confidence": self.confidence,
                "model_version": "v3.0_mixture",
            })

        return payload
