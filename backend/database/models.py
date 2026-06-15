from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, ForeignKey, Enum, JSON, event
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func
import enum

from database.config import get_settings

settings = get_settings()

_engine_kwargs = {
    "pool_pre_ping": True
}
if "sqlite" in settings.DATABASE_URL:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    # SQLite SingletonThreadPool does not support pool_size, max_overflow, pool_timeout
    # Only set them for PostgreSQL or other DBs
    if settings.DATABASE_URL == "sqlite:///:memory:":
        from sqlalchemy.pool import StaticPool
        _engine_kwargs["poolclass"] = StaticPool
else:
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
    _engine_kwargs["pool_timeout"] = settings.DB_POOL_TIMEOUT

engine = create_engine(
    settings.DATABASE_URL,
    **_engine_kwargs
)



@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """启用 WAL 模式 + 忙等待超时，提升 SQLite 并发写入性能"""
    if "sqlite" not in settings.DATABASE_URL:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ────────────────────────────
# Enums
# ────────────────────────────
class MatchStatus(str, enum.Enum):
    SCHEDULED = "scheduled"  # 已排期
    UPCOMING = "upcoming"    # 即将开始（48h内）
    LIVE = "live"            # 进行中
    FINISHED = "finished"    # 已结束
    POSTPONED = "postponed"  # 延期


class MatchType(str, enum.Enum):
    WORLD_CUP = "world_cup"   # 世界杯正赛
    FRIENDLY = "friendly"     # 友谊赛
    WARM_UP = "warm_up"       # 热身赛
    QUALIFIER = "qualifier"   # 预选赛


class PlayType(str, enum.Enum):
    SPF = "SPF"       # 胜平负
    RQ = "RQ"         # 让球胜平负
    SCORE = "SCORE"   # 比分
    GOALS = "GOALS"   # 总进球
    HALF = "HALF"     # 半全场


class LicenseType(str, enum.Enum):
    MATCH = "match"           # 单场
    TOURNAMENT = "tournament" # 届卡


# ────────────────────────────
# User
# ────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)
    paid_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    redemptions = relationship("LicenseRedemption", back_populates="user")


# ────────────────────────────
# 用户设置
# ────────────────────────────
class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    risk_tier = Column(String(20), default="balanced")  # conservative / balanced / aggressive / speculative
    default_play_type = Column(String(20), default="SPF")  # spf / rq / score / goals / half
    show_ev = Column(Boolean, default=True)
    show_probability = Column(Boolean, default=True)
    notify_odds_change = Column(Boolean, default=False)
    notify_match_start = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", backref="settings")


# ────────────────────────────
# License Key (卡密系统)
# ────────────────────────────
class LicenseKey(Base):
    __tablename__ = "license_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    license_type = Column(Enum(LicenseType), nullable=False)
    # match_id: 如果 type=MATCH，对应具体比赛
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    # tournament: 如果 type=TOURNAMENT，标记哪个赛事
    tournament = Column(String(50), default="WC2026")
    is_used = Column(Boolean, default=False)
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    match = relationship("Match")
    redemptions = relationship("LicenseRedemption", back_populates="license")


class LicenseRedemption(Base):
    __tablename__ = "license_redemptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    license_id = Column(Integer, ForeignKey("license_keys.id"), nullable=False)
    redeemed_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="redemptions")
    license = relationship("LicenseKey", back_populates="redemptions")


# ────────────────────────────
# AI 专属数据模型 (Multi-Tenant AI)
# ────────────────────────────
class UserQuantProfile(Base):
    """用户的量化偏好档案（千人千面）"""
    __tablename__ = "user_quant_profiles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    base_bankroll = Column(Float, default=1000.0)
    risk_tolerance = Column(String(50), default="balanced") # strict, balanced, aggressive
    preferred_leagues = Column(JSON, default=list) # e.g., ["EPL", "LaLiga"]
    ai_behavior_prompt = Column(Text, nullable=True) # 用户专属的 AI 隐藏提示词
    
    user = relationship("User")

class AIChatSession(Base):
    """用户的 AI 对话会话（支持多轮上下文）"""
    __tablename__ = "ai_chat_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), default="新分析会话")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User")
    messages = relationship("AIMessage", back_populates="session", cascade="all, delete-orphan", order_by="AIMessage.created_at")

class AIMessage(Base):
    """对话内容与数据快照"""
    __tablename__ = "ai_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(50), nullable=False) # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    context_snapshot = Column(JSON, nullable=True) # 提问或回答时系统所引用的盘口和模型数据
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    session = relationship("AIChatSession", back_populates="messages")

class AIInteractionFeedback(Base):
    """用于强化学习的反馈环"""
    __tablename__ = "ai_interaction_feedback"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("ai_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_helpful = Column(Boolean, nullable=False)
    feedback_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    message = relationship("AIMessage")
    user = relationship("User")

# ────────────────────────────
# Team (队名合并与清理准备)
# ────────────────────────────
class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=True)
    code = Column(String(10), unique=True, nullable=False)  # FIFA code, e.g., ARG
    flag = Column(String(10), default="🏳️")
    fifa_rank = Column(Integer, nullable=True)
    elo = Column(Integer, nullable=True)
    group = Column(String(10), nullable=True)  # A, B, C...
    continent = Column(String(50), nullable=True)

    # ─── 动态统计 ───
    squad_size = Column(Integer, default=0)
    form_last5 = Column(String(10), default="")  # e.g., "WWDWL"
    form_factor = Column(Float, default=1.0)  # 状态因子 0.5~1.5
    avg_goals_scored = Column(Float, default=1.30)  # 近N场场均进球
    avg_goals_conceded = Column(Float, default=1.10)  # 近N场场均失球

    # ─── FBref / SoccerData 高级统计（自动同步） ───
    avg_xg = Column(Float, nullable=True)  # 场均期望进球 xG
    avg_xga = Column(Float, nullable=True)  # 场均期望失球 xGA
    possession = Column(Float, nullable=True)  # 平均控球率 %
    pass_completion = Column(Float, nullable=True)  # 传球成功率 %
    shots_per_game = Column(Float, nullable=True)  # 场均射门次数
    stats_synced_at = Column(DateTime(timezone=True), nullable=True)  # 上次同步时间

    # ─── 近期战绩详情（近10场） ───
    recent_results = Column(String(20), default="")  # "WWDLWLLDWD"
    recent_goals_scored = Column(Float, default=0)
    recent_goals_conceded = Column(Float, default=0)

    # ─── 主客场与气候 ───
    home_away_factor = Column(Float, default=1.0)  # 1.0=中性, >1 主场强
    weather_adaptability = Column(Float, default=1.0)  # 0~2, 越高适应力越强

    # ─── 战术与教练 ───
    tactical_style = Column(String(20), default="balanced")  # attack/defense/balanced/counter
    coach_rating = Column(Float, default=0.5)  # 0~1, 越高临场能力越强

    # ─── 赛程与疲劳 ───
    rest_days = Column(Integer, default=7)  # 距离上场比赛天数
    key_injuries = Column(String(200), default="")  # 核心伤停名单, e.g. "梅西(伤),内马尔(停)"
    squad_fatigue_index = Column(Float, default=0.5)  # 0=充沛, 1=极度疲劳
    key_players_available = Column(Integer, default=11)  # 可用核心球员数
    key_players_total = Column(Integer, default=11)  # 核心球员总数

    # Relationships
    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")


# ────────────────────────────
# Match
# ────────────────────────────
class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    match_code = Column(String(50), unique=True, nullable=False)  # WC2026-A1

    # Teams
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)

    # Schedule
    kickoff_at = Column(DateTime(timezone=True), nullable=True, index=True)
    group = Column(String(10), nullable=True, index=True)
    stage = Column(String(50), default="group")  # group / R32 / R16 / QF / SF / F
    venue = Column(String(100), nullable=True)

    # Match type & competition
    match_type = Column(Enum(MatchType), default=MatchType.WORLD_CUP)
    competition = Column(String(100), nullable=True)  # WC2026 / International Friendly

    # Status
    status = Column(Enum(MatchStatus), default=MatchStatus.SCHEDULED, index=True)

    # ─── 比赛环境（影响预测） ───
    venue_type = Column(String(20), default="neutral")  # home / away / neutral
    weather = Column(String(20), default="clear")  # clear / rain / hot / cold / snow
    temperature = Column(Float, default=20.0)  # 摄氏度
    pitch_condition = Column(String(20), default="good")  # good / average / poor / artificial
    schedule_density = Column(String(20), default="normal")  # light / normal / dense / extreme

    # Odds (current best snapshot)
    odds_home = Column(Float, nullable=True)
    odds_draw = Column(Float, nullable=True)
    odds_away = Column(Float, nullable=True)
    odds_source = Column(String(100), nullable=True)  # synthetic / betexplorer / oddsapi / football-data / manual

    # ─── 开盘赔率（最早的真实赔率快照，用于赔率变化分析） ───
    opening_odds_home = Column(Float, nullable=True)
    opening_odds_draw = Column(Float, nullable=True)
    opening_odds_away = Column(Float, nullable=True)
    opening_odds_source = Column(String(100), nullable=True)
    opening_odds_at = Column(DateTime(timezone=True), nullable=True)

    # ─── 收盘赔率（赛前最后采集的真实赔率，与实时odds分离） ───
    closing_odds_home = Column(Float, nullable=True)
    closing_odds_draw = Column(Float, nullable=True)
    closing_odds_away = Column(Float, nullable=True)
    closing_odds_source = Column(String(100), nullable=True)
    odds_locked_at = Column(DateTime(timezone=True), nullable=True)  # 赔率锁定时间

    # Model confidence
    confidence = Column(String(10), nullable=True)  # high / medium / low
    odds_degraded = Column(Boolean, default=False)  # True if prediction lacks market odds

    # Actual result (filled after match)
    actual_home_goals = Column(Integer, nullable=True)
    actual_away_goals = Column(Integer, nullable=True)
    actual_outcome = Column(String(10), nullable=True)  # home / draw / away

    # Half-time result (from openfootball score.ht)
    ht_home_goals = Column(Integer, nullable=True)
    ht_away_goals = Column(Integer, nullable=True)

    # ─── AI Content ───
    poster_url = Column(String(500), nullable=True)  # AI 生成的赛事海报链接
    is_broadcasted = Column(Boolean, default=False)  # 是否已执行社交媒体自动播报

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    predictions = relationship("Prediction", back_populates="match")
    odds_history = relationship("OddsHistory", back_populates="match")
    bookmaker_odds = relationship("MatchBookmakerOdds", back_populates="match")
    live_odds = relationship("LiveOddsSnapshot", back_populates="match")


# ────────────────────────────
# Prediction (赛前锁定快照)
# ────────────────────────────
class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)

    # Play type
    play_type = Column(Enum(PlayType), nullable=False, index=True)

    # Prediction data (JSON for flexibility)
    # e.g., for SPF: {"home": 0.62, "draw": 0.20, "away": 0.18}
    probabilities = Column(JSON, nullable=False)

    # Model version & inputs reference
    model_version = Column(String(20), default="v1.0")
    confidence = Column(String(10), nullable=True)  # high / medium / low
    input_checksum = Column(String(64), nullable=True)  # sha256 of inputs

    # Lock time
    locked_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    match = relationship("Match", back_populates="predictions")


# ────────────────────────────
# Odds History（赔率时间序列，用于收盘赔率判定和异动分析）
# ────────────────────────────
class OddsHistory(Base):
    __tablename__ = "odds_history"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    source = Column(String(20), nullable=False)  # betexplorer / oddsapi / football-data / jingcai / synthetic
    odds_home = Column(Float, nullable=False)
    odds_draw = Column(Float, nullable=False)
    odds_away = Column(Float, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_closing = Column(Boolean, default=False)  # 是否为收盘赔率（赛前最后一次真实采集）
    is_real = Column(Boolean, default=True)  # synthetic=False, 真实源=True

    match = relationship("Match", back_populates="odds_history")


# ────────────────────────────
# Live Odds Snapshot（滚球赔率快照）
# ────────────────────────────
class LiveOddsSnapshot(Base):
    __tablename__ = "live_odds_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    source = Column(String(20), nullable=False)  # oddsapi / betexplorer / jingcai
    odds_home = Column(Float, nullable=False)
    odds_draw = Column(Float, nullable=False)
    odds_away = Column(Float, nullable=False)
    match_minute = Column(Integer, nullable=True)  # 比赛进行到第几分钟
    live_score_home = Column(Integer, nullable=True)  # 当前比分(主)
    live_score_away = Column(Integer, nullable=True)  # 当前比分(客)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    match = relationship("Match", back_populates="live_odds")


# ────────────────────────────
# Player Stats（FBref / SoccerData 球员统计）
# ────────────────────────────
class PlayerStats(Base):
    __tablename__ = "player_stats"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    player_name = Column(String(100), nullable=False)
    season = Column(String(20), nullable=False)  # e.g., "2022"
    league = Column(String(50), nullable=True)

    minutes = Column(Integer, default=0)
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    xg = Column(Float, nullable=True)  # expected goals
    xa = Column(Float, nullable=True)  # expected assists
    shots = Column(Integer, default=0)
    key_passes = Column(Integer, default=0)
    yellow_cards = Column(Integer, default=0)
    red_cards = Column(Integer, default=0)

    source = Column(String(20), default="fbref")  # fbref / understat / whoscored
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


# ────────────────────────────
# Match Bookmaker Odds（多庄家赔率，用于市场模型和回测）
# ────────────────────────────
class MatchBookmakerOdds(Base):
    __tablename__ = "match_bookmaker_odds"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    bookmaker = Column(String(30), nullable=False)  # b365 / pinnacle / williamhill / betfair
    odds_home = Column(Float, nullable=False)
    odds_draw = Column(Float, nullable=False)
    odds_away = Column(Float, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_closing = Column(Boolean, default=False)  # 是否为该庄家的收盘赔率

    match = relationship("Match", back_populates="bookmaker_odds")


# ────────────────────────────
# Fusion Weight（从历史数据回归学习的动态权重）
# ────────────────────────────
class FusionWeight(Base):
    __tablename__ = "fusion_weights"

    id = Column(Integer, primary_key=True, index=True)
    stage = Column(String(20), default="all")  # all / group / R16 / QF / SF / F
    elo_diff_range = Column(String(20), default="all")  # all / 0-100 / 100-200 / 200-400 / 400+
    weights = Column(JSON, nullable=False)  # {"elo": 0.1, "poisson": 0.55, ...}
    metric = Column(String(20), default="brier")  # 优化目标: brier / log_loss / accuracy
    metric_value = Column(Float, nullable=True)  # 该权重在验证集上的表现
    sample_size = Column(Integer, default=0)  # 训练样本数
    is_active = Column(Boolean, default=True)
    learned_at = Column(DateTime(timezone=True), server_default=func.now())


# ────────────────────────────
# Audit Log
# ────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    audit_id = Column(String(64), unique=True, nullable=False)
    data_type = Column(String(50), nullable=False)  # odds / lineup / prediction
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    source_name = Column(String(50), nullable=True)
    source_type = Column(String(20), default="api")  # api / scrapy / manual
    raw_payload = Column(Text, nullable=True)
    parsed_value = Column(JSON, nullable=True)
    source_timestamp = Column(DateTime(timezone=True), nullable=True)
    ingest_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    processor = Column(String(50), nullable=True)
    checksum = Column(String(128), nullable=True)


# ────────────────────────────
# 准确性快照 — 生产环境/回测的预测准确性持久化
# ────────────────────────────
class AccuracySnapshot(Base):
    __tablename__ = "accuracy_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_type = Column(String(20), default="daily")  # daily / weekly / backtest / walk_forward
    metric = Column(String(20), default="brier")  # brier / log_loss / direction_accuracy
    value = Column(Float, nullable=False)
    sample_size = Column(Integer, default=0)
    weights = Column(JSON, nullable=True)  # 当时使用的融合权重
    stage = Column(String(20), default="all")  # all / group / R16 / QF / SF / F
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ────────────────────────────
# 预测快照 — 赛前预测锁定（防篡改审计）
# ────────────────────────────
class PredictionSnapshot(Base):
    __tablename__ = "prediction_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    snapshot_json = Column(Text, nullable=False)  # 完整预测结果JSON
    checksum = Column(String(64), nullable=False)  # SHA-256校验和
    model_version = Column(String(20), nullable=True)  # 当时使用的模型版本
    locked_at = Column(DateTime(timezone=True), server_default=func.now())  # 锁定时间
    is_locked = Column(Boolean, default=True)  # 是否已锁定（不可修改）

    match = relationship("Match")


# ────────────────────────────
# Jingcai Issue（中国足彩期号）
# ────────────────────────────
class JingcaiIssue(Base):
    __tablename__ = "jingcai_issues"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(String(20), unique=True, nullable=False, index=True)  # e.g. "25060"
    issue_type = Column(String(20), default="spf14")  # spf14 / r9 / half6 / goals4
    status = Column(String(20), default="on_sale")  # on_sale / locked / drawn / verified

    # 销售/开奖时间
    sale_start = Column(DateTime(timezone=True), nullable=True)
    sale_end = Column(DateTime(timezone=True), nullable=True)
    draw_at = Column(DateTime(timezone=True), nullable=True)

    # 开奖结果（JSON，不同玩法格式不同）
    # spf14: {"results": ["3","1","0",...], "prizes": {"1st": {"winners": 10, "amount": 5000000}, ...}}
    draw_result = Column(JSON, nullable=True)

    # 模型预测验证结果（开奖后对比）
    verification = Column(JSON, nullable=True)
    # e.g. {"spf_hits": 12, "r9_hits": 8, "predicted_correct": [true, false, ...]}

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    issue_matches = relationship("JingcaiIssueMatch", back_populates="issue", cascade="all, delete-orphan")


class JingcaiIssueMatch(Base):
    """足彩期号与比赛的关联（一场期号包含多场比赛）"""
    __tablename__ = "jingcai_issue_matches"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("jingcai_issues.id"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    sequence = Column(Integer, default=0)  # 场次序号（1-14）
    handicap = Column(Integer, default=0)  # 让球数（如 -1, 0, +1）

    # 多玩法竞彩赔率（JSON 格式）
    rq_odds = Column(Text, nullable=True)  # 让球赔率: {"h":"1.95","d":"3.40","a":"3.10"}
    score_odds = Column(Text, nullable=True)  # 比分赔率: {"s01s00":"8.00",...}
    goals_odds = Column(Text, nullable=True)  # 进球赔率: {"s0":"12.00","s1":"5.00",...}
    half_odds = Column(Text, nullable=True)  # 半全场赔率: {"hh":"4.10","dd":"5.90",...}

    # Relationships
    issue = relationship("JingcaiIssue", back_populates="issue_matches")
    match = relationship("Match")


# ────────────────────────────
# 用户留言板
# ────────────────────────────
class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    category = Column(String(30), nullable=False)  # suggestion / bug / data_issue / discussion
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    content = Column(Text, nullable=False)
    is_anonymous = Column(Boolean, default=False)
    likes = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="feedbacks")
    match = relationship("Match")


class FeedbackLike(Base):
    __tablename__ = "feedback_likes"

    id = Column(Integer, primary_key=True, index=True)
    feedback_id = Column(Integer, ForeignKey("feedbacks.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ────────────────────────────
# AI Report Cache（AI 精算师报告持久化缓存，解决并发性能瓶颈）
# ────────────────────────────
class MatchAIReport(Base):
    __tablename__ = "match_ai_reports"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), unique=True, index=True)
    content = Column(Text, nullable=False)
    input_checksum = Column(String(64), nullable=True) # 用于感知赔率变化以更新报告
    generated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

# ────────────────────────────
# Betting Exchange Volume（必发交易所资金冷热监控数据）
# ────────────────────────────
class BettingExchangeVolume(Base):
    __tablename__ = "betting_exchange_volumes"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), unique=True, index=True)
    total_volume = Column(Float, default=0.0)
    home_ratio = Column(Float, default=0.0)
    draw_ratio = Column(Float, default=0.0)
    away_ratio = Column(Float, default=0.0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

