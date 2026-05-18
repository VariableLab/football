import re
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime


# ─── User ───
class UserRegister(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be at most 128 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    is_paid: bool
    paid_until: Optional[datetime]
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ─── License Key ───
class LicenseKeyCreate(BaseModel):
    license_type: str          # "match" or "tournament"
    match_id: Optional[int] = None
    count: int = 1


class LicenseKeyOut(BaseModel):
    key: str
    license_type: str
    is_used: bool
    created_at: datetime


class LicenseRedeem(BaseModel):
    key: str


class LicenseRedeemOut(BaseModel):
    success: bool
    license_type: str
    message: str


# ─── Team ───
class TeamCreate(BaseModel):
    name: str
    name_en: Optional[str] = None
    code: str
    flag: str = "🏳️"
    fifa_rank: Optional[int] = None
    elo: Optional[int] = None
    group: Optional[str] = None
    continent: Optional[str] = None


class TeamOut(BaseModel):
    id: int
    name: str
    name_en: Optional[str]
    code: str
    flag: Optional[str] = None
    fifa_rank: Optional[int]
    elo: Optional[int]
    group: Optional[str]
    form_factor: Optional[float]
    avg_goals_scored: Optional[float]
    avg_goals_conceded: Optional[float]

    # ─── 扩展字段 ───
    recent_results: Optional[str] = None
    recent_goals_scored: Optional[float] = None
    recent_goals_conceded: Optional[float] = None
    home_away_factor: Optional[float] = None
    weather_adaptability: Optional[float] = None
    tactical_style: Optional[str] = None
    coach_rating: Optional[float] = None
    rest_days: Optional[int] = None
    key_injuries: Optional[str] = None
    squad_fatigue_index: Optional[float] = None

    class Config:
        from_attributes = True


# ─── Match ───
class MatchCreate(BaseModel):
    match_code: str
    home_team_id: int
    away_team_id: int
    kickoff_at: Optional[datetime] = None
    group: Optional[str] = None
    stage: str = "group"
    venue: Optional[str] = None


class MatchUpdateResult(BaseModel):
    actual_home_goals: int
    actual_away_goals: int


# ─── Prediction ───
class PredictionCreate(BaseModel):
    match_id: int
    play_type: str
    probabilities: Dict[str, Any]
    model_version: str = "v1.0"
    model_config = ConfigDict(protected_namespaces=())


class PredictionOut(BaseModel):
    id: int
    match_id: int
    play_type: str
    probabilities: Dict[str, Any]
    model_version: str
    input_checksum: Optional[str] = None
    locked_at: Optional[datetime] = None
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)


class MatchOut(BaseModel):
    id: int
    match_code: str
    home_team: TeamOut
    away_team: TeamOut
    kickoff_at: Optional[datetime]
    kickoff_bj: Optional[str] = None
    group: Optional[str]
    stage: Optional[str] = None
    match_type: Optional[str] = None
    competition: Optional[str]
    status: str
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    odds_source: Optional[str] = None
    confidence: Optional[str] = None
    odds_degraded: Optional[bool] = False
    updated_at: Optional[datetime] = None

    # ─── 比赛环境 ───
    venue_type: Optional[str] = None
    weather: Optional[str] = None
    temperature: Optional[float] = None
    pitch_condition: Optional[str] = None
    schedule_density: Optional[str] = None

    actual_home_goals: Optional[int]
    actual_away_goals: Optional[int]
    actual_outcome: Optional[str]
    predictions: List[PredictionOut] = []

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)

    @field_validator('kickoff_bj', mode='before')
    @classmethod
    def compute_bj_time(cls, v, info):
        kickoff = info.data.get('kickoff_at') if hasattr(info, 'data') else None
        if kickoff and v is None:
            from datetime import timezone, timedelta
            try:
                bj = kickoff.astimezone(timezone(timedelta(hours=8)))
                return bj.strftime("%m月%d日 %H:%M")
            except Exception:
                return None
        return v


# ─── Strategy ───
class StrategyPickOut(BaseModel):
    strategy_name: str
    strategy_type: str
    play_type: str
    play_label: str
    selection: str
    selection_label: str
    probability: float
    odds: float
    ev: float
    kelly_fraction: float
    stake_pct: float
    confidence: str
    rationale: str
    risk_level: str
    # 校准管线新增字段
    risk_tier: Optional[str] = None
    model_prob_calibrated: Optional[float] = None
    market_prob: Optional[float] = None
    edge: Optional[float] = None
    var_95: Optional[float] = None
    cvar_95: Optional[float] = None
    risk_score: Optional[float] = None
    is_recommended: Optional[bool] = None

    model_config = ConfigDict(protected_namespaces=())


# ─── Dashboard ───
class DashboardStats(BaseModel):
    total_matches: int
    finished_matches: int
    total_predictions: int
    prediction_accuracy: float
    total_users: int
    paid_users: int


# ─── Jingcai Issue ───
class JingcaiIssueCreate(BaseModel):
    issue_id: str
    issue_type: str = "spf14"
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    match_codes: List[str] = []  # 比赛 match_code 列表


class JingcaiIssueMatchOut(BaseModel):
    sequence: int
    handicap: int
    rq_odds: Optional[Dict[str, float]] = None
    score_odds: Optional[Dict[str, float]] = None
    goals_odds: Optional[Dict[str, float]] = None
    half_odds: Optional[Dict[str, float]] = None
    match: MatchOut

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)


class JingcaiIssueOut(BaseModel):
    id: int
    issue_id: str
    issue_type: str
    status: str
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    draw_at: Optional[datetime] = None
    draw_result: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    matches: List[JingcaiIssueMatchOut] = []

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)


class JingcaiIssueResultIn(BaseModel):
    results: List[str]  # 如 ["3","1","0","3",...] 对应每场结果
    prizes: Optional[Dict[str, Any]] = None
    draw_at: Optional[datetime] = None


# ─── Generic List Wrapper ───
class ListResponse(BaseModel):
    total: int
    offset: int = 0
    limit: int = 50
    items: List[Any]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class MatchListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[MatchOut]


class JingcaiIssueListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[JingcaiIssueOut]


# ─── Strategy Response ───
class StrategyResponse(BaseModel):
    match_id: int
    status: str
    confidence: str = "medium"
    odds_degraded: bool = False
    risk_tier: str = "balanced"
    strategies: List[StrategyPickOut]
    predictions: List[PredictionOut]


# ─── Feedback ───
class FeedbackOut(BaseModel):
    id: int
    category: str
    match_id: Optional[int] = None
    content: str
    is_anonymous: bool = False
    likes: int = 0
    author: str = "匿名用户"
    created_at: Optional[str] = None


class FeedbackListResponse(BaseModel):
    items: List[FeedbackOut]
    total: int


# ─── Odds Movement ───
class SteamMoveOut(BaseModel):
    selection: str
    from_odds: float
    to_odds: float
    change_pct: float
    window_minutes: float
    direction: str


class LateMoneyOut(BaseModel):
    selection: str
    from_odds: float
    to_odds: float
    change_pct: float
    direction: str


class OddsMovementResponse(BaseModel):
    match_id: int
    has_opening: bool
    opening_odds: Optional[Dict[str, Any]] = None
    closing_odds: Optional[Dict[str, Any]] = None
    drift: Dict[str, Any]
    steam_moves: List[SteamMoveOut] = []
    late_money: List[LateMoneyOut] = []
    signal: Optional[str] = None
    snapshots: Dict[str, int]


# ─── Arbitrage ───
class ArbitrageOpportunity(BaseModel):
    match_id: int
    best_odds: Dict[str, float]
    bookmakers: Dict[str, Optional[str]]
    implied_total: float
    profit_pct: float
    net_profit_pct: float
    stakes: Dict[str, float]
    is_genuine: bool


class ArbitrageResponse(BaseModel):
    count: int
    opportunities: List[ArbitrageOpportunity]


# ─── Health ───
class HealthCheck(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    checks: Dict[str, str]


# ─── Validation ───
class CalibrationPoint(BaseModel):
    bin_center: float
    fraction_positive: float
    count: int


class PlayTypeAccuracy(BaseModel):
    play_type: str
    total: int
    correct: int
    accuracy: float
    brier: Optional[float] = None


# ─── Jingcai Report Item ───
class BestPickOut(BaseModel):
    play_type: str
    selection: str
    probability: float


class JingcaiReportMatch(BaseModel):
    sequence: int
    home: str
    away: str
    handicap: int = 0
    kickoff_at: Optional[str] = None
    best_pick: Optional[BestPickOut] = None
    actual_outcome: Optional[str] = None
    actual_home_goals: Optional[int] = None
    actual_away_goals: Optional[int] = None
    correct: Optional[bool] = None


class JingcaiReportIssue(BaseModel):
    issue_id: str
    issue_type: str
    status: str
    sale_end: Optional[str] = None
    draw_at: Optional[str] = None
    total_matches: int
    spf_hits: int
    accuracy: float
    r9_hits: int = 0
    analysis: str = ""
    matches: List[JingcaiReportMatch]


class JingcaiReportResponse(BaseModel):
    reports: List[JingcaiReportIssue]


# ─── LiveOdds SSE ───
class LiveOddsUpdateOut(BaseModel):
    match_id: int
    odds_home: float
    odds_draw: float
    odds_away: float
    match_minute: Optional[int] = None
    live_score_home: Optional[int] = None
    live_score_away: Optional[int] = None
    source: str
    recorded_at: str


# ─── Team List ───
class TeamListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[TeamOut]


# ─── Validation ───
class ValidationSummary(BaseModel):
    total_matches: int
    finished_matches: int
    validated_matches: int
    direction_accuracy: float
    high_conf_accuracy: float
    medium_conf_accuracy: float
    low_conf_accuracy: float
    avg_brier_score: float
    avg_log_loss: float
    avg_max_prob: float


class ValidationMatchItem(BaseModel):
    match_id: int
    match_code: str
    home_team: str
    away_team: str
    actual_outcome: str
    predicted_outcome: str
    probabilities: Dict[str, float]
    direction_correct: bool
    brier_score: float
    log_loss: float
    max_prob: float
    confidence: str
    actual_score: Optional[str] = None
    predicted_top_score: Optional[str] = None
    score_correct: bool = False


class ValidationReportResponse(BaseModel):
    summary: ValidationSummary
    by_play_type: Dict[str, Any]
    matches: List[ValidationMatchItem]
    generated_at: str


class CalibrationBin(BaseModel):
    bin: str
    avg_predicted_prob: float
    actual_accuracy: float
    sample_size: int


class CalibrationCurveResponse(BaseModel):
    curve: List[CalibrationBin]
    ece: float
    total_samples: int


class PlayTypeBreakdownItem(BaseModel):
    label: str
    total: int
    correct: int
    accuracy: Optional[float] = None


class PlayTypeBreakdownResponse(BaseModel):
    SPF: PlayTypeBreakdownItem
    RQ: PlayTypeBreakdownItem
    SCORE: PlayTypeBreakdownItem
    GOALS: PlayTypeBreakdownItem
    HALF: PlayTypeBreakdownItem


# ─── Settings ───
class SettingsResponse(BaseModel):
    risk_tier: str
    default_play_type: str
    show_ev: bool
    show_probability: bool
    notify_odds_change: bool
    notify_match_start: bool


class SettingsUpdateResponse(BaseModel):
    status: str


# ─── Bet NN ───
class BetNNStatusResponse(BaseModel):
    trained: bool
    ready: bool
    trained_at: Optional[str] = None
    final_val_accuracy: Optional[float] = None
    epochs: Optional[int] = None


class BetNNPredictResponse(BaseModel):
    ready: bool = False
    message: Optional[str] = None
    match_id: Optional[int] = None
    match_code: Optional[str] = None
    bet_values: Optional[Dict[str, float]] = None
    recommended: Optional[str] = None
    recommended_label: Optional[str] = None
    confidence: Optional[float] = None
    model_version: Optional[str] = None


class BetNNTrainResponse(BaseModel):
    status: str
    message: Optional[str] = None
    epochs_trained: Optional[int] = None
    best_val_loss: Optional[float] = None
    final_val_accuracy: Optional[float] = None
    samples: Optional[int] = None


# ─── Feedback Like ───
class FeedbackLikeResponse(BaseModel):
    id: int
    likes: int
    action: str


# ─── LiveOdds Detail ───
class LiveOddsUpdateData(BaseModel):
    match_id: int
    source: str
    odds: Dict[str, float]
    match_minute: Optional[int] = None
    score: Optional[Dict[str, int]] = None
    changes: Dict[str, float]
    timestamp: str


class LiveOddsSingleResponse(BaseModel):
    match_id: int
    latest: Optional[LiveOddsUpdateData] = None
    history: List[LiveOddsUpdateData]


class LiveOddsAllResponse(BaseModel):
    count: int
    updates: Dict[str, LiveOddsUpdateData]


# ─── Live Hedge ───
class HedgeAlertItem(BaseModel):
    match_id: int
    level: str
    type: str
    message: str
    current_odds: Dict[str, float]
    profit_pct: float
    timestamp: str


class HedgePositionValue(BaseModel):
    selection: str
    odds: float
    stake: float


class HedgeAlertsResponse(BaseModel):
    alerts: List[HedgeAlertItem]
    positions: Dict[str, HedgePositionValue]


class HedgePositionResponse(BaseModel):
    status: str
    match_id: int


class HedgeComputeResult(BaseModel):
    match_id: int
    hedge_available: bool
    hedge_stake: Optional[float] = None
    hedge_odds: Optional[float] = None
    guaranteed_profit: Optional[float] = None
    hedge_ratio: Optional[float] = None
    profit_if_original_wins: Optional[float] = None
    profit_if_hedge_wins: Optional[float] = None
    is_profitable: Optional[bool] = None


# ─── Optimal Combo ───
class OptimalComboPick(BaseModel):
    match_id: int
    match_code: str
    home: str
    away: str
    kickoff_at: Optional[str] = None
    play_type: str
    selection: str
    selection_label: str
    probability: float
    odds: float
    ev: float
    handicap: Optional[int] = None
    rationale: str


class OptimalComboResponse(BaseModel):
    issue_id: int
    picks: List[OptimalComboPick]
    total: int


# ─── Generic Status ───
class StatusResponse(BaseModel):
    status: str


class FeedbackCreateResponse(BaseModel):
    id: int
    status: str
