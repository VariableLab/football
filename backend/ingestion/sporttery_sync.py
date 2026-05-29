"""
sporttery.cn 数据同步模块 — 主力数据源

功能:
1. 每日增量同步在售比赛 → 创建/更新 Match + Team + JingcaiIssue
2. 写入开盘/实时/收盘赔率到 Match.odds_* 字段 (odds_source="sporttery")
3. 让球/比分/进球/半全场赔率写入 JingcaiIssueMatch
4. 比赛结束后从 openfootball 同步结果（sporttery.cn 不提供历史结果）
5. 注册为调度器定时任务

数据源: https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry
"""
import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from database.models import (
    SessionLocal, Team, Match, MatchStatus, MatchType,
    JingcaiIssue, JingcaiIssueMatch, OddsHistory,
)
from odds_collector import JingcaiSource
from utils.logger import get_logger

logger = get_logger("sporttery_sync")

# ────────────────────────────
# 中文别名 → 标准 code 映射
# ────────────────────────────
# 直接引用 jingcai_predictor 的映射，避免重复维护
def _get_team_resolver():
    """延迟加载 jingcai_predictor 的球队解析器"""
    from jingcai_predictor import resolve_team_code, CLUB_ELO_RATINGS, CHINESE_ALIASES
    return resolve_team_code, CLUB_ELO_RATINGS, CHINESE_ALIASES


# ────────────────────────────
# 同步结果数据类
# ────────────────────────────
class SyncResult:
    """同步操作结果统计"""
    def __init__(self) -> None:
        self.matches_created: int = 0
        self.matches_updated: int = 0
        self.odds_written: int = 0
        self.opening_set: int = 0
        self.closing_set: int = 0
        self.issues_created: int = 0
        self.issue_matches_created: int = 0
        self.teams_created: int = 0
        self.predictions_generated: int = 0
        self.errors: List[str] = []

    def summary(self) -> str:
        lines = [
            f"新建比赛: {self.matches_created}",
            f"更新赔率: {self.matches_updated}",
            f"赔率写入: {self.odds_written}",
            f"开盘价设置: {self.opening_set}",
            f"收盘价设置: {self.closing_set}",
            f"新建期号: {self.issues_created}",
            f"期号比赛关联: {self.issue_matches_created}",
            f"新建球队: {self.teams_created}",
            f"生成预测: {self.predictions_generated}",
        ]
        if self.errors:
            lines.append(f"错误: {len(self.errors)}")
        return "\n".join(lines)


# ────────────────────────────
# 核心同步逻辑
# ────────────────────────────

def sync_from_sporttery(days_ahead: int = 3, generate_predictions: bool = True) -> SyncResult:
    """
    从 sporttery.cn 同步在售比赛数据到数据库。

    流程:
    1. 调用 sporttery.cn API 获取所有在售比赛
    2. 按队名查找/创建 Team 记录
    3. 按比赛编码查找/创建 Match 记录
    4. 写入 SPF/RQ/比分/进球/半全场赔率
    5. 首次出现 → 设置 opening_odds，开赛后 → 设置 closing_odds
    6. 创建 JingcaiIssue + JingcaiIssueMatch 关联
    7. 为新建比赛生成预测（可选）
    """
    result = SyncResult()
    resolve_team_code, CLUB_ELO_RATINGS, CHINESE_ALIASES = _get_team_resolver()

    src = JingcaiSource()
    db = SessionLocal()

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        logger.info(f"[sporttery] 正在同步 {today} ~ {end_date} 在售比赛...")
        api_data = src._fetch_all_pools(today, end_date)

        if not api_data:
            logger.warning("[sporttery] 未获取到任何比赛数据")
            return result

        logger.info(f"[sporttery] 获取到 {len(api_data)} 场在售比赛")

        now = datetime.now(timezone.utc)

        for mid, mdata in api_data.items():
            try:
                _sync_single_match(
                    db=db,
                    mdata=mdata,
                    now=now,
                    result=result,
                    resolve_team_code=resolve_team_code,
                    club_elo_ratings=CLUB_ELO_RATINGS,
                    generate_predictions=generate_predictions,
                )
            except Exception as e:
                err_msg = f"matchId={mid}, home={mdata.get('homeTeamAbbName')}, away={mdata.get('awayTeamAbbName')}: {e}"
                logger.warning(f"[sporttery] 同步失败: {err_msg}")
                result.errors.append(err_msg)
                db.rollback()

        db.commit()
        logger.info(f"[sporttery] 同步完成:\n{result.summary()}")

    except Exception as e:
        db.rollback()
        logger.error(f"[sporttery] 同步异常: {e}")
        result.errors.append(str(e))
    finally:
        db.close()
        src.close()

    return result


def _sync_single_match(
    db,
    mdata: Dict[str, Any],
    now: datetime,
    result: SyncResult,
    resolve_team_code,
    club_elo_ratings: dict,
    generate_predictions: bool = True,
) -> None:
    """同步单场比赛数据"""
    home_cn = mdata.get("homeTeamAbbName", "") or mdata.get("homeTeamAllName", "")
    away_cn = mdata.get("awayTeamAbbName", "") or mdata.get("awayTeamAllName", "")
    home_code = mdata.get("homeTeamCode", "")
    away_code = mdata.get("awayTeamCode", "")
    league = mdata.get("leagueAbbName", "") or mdata.get("leagueAllName", "")
    match_date = mdata.get("matchDate", "")
    match_time = mdata.get("matchTime", "")

    had = mdata.get("had", {})
    hhad = mdata.get("hhad", {})

    # 解析 SPF 赔率
    odds_h = _safe_float(had.get("h"))
    odds_d = _safe_float(had.get("d"))
    odds_a = _safe_float(had.get("a"))

    # 预写入校验: 赔率范围检查
    from data_cleaner import validate_odds
    odds_h, odds_d, odds_a, odds_valid = validate_odds(odds_h, odds_d, odds_a)
    if not odds_valid:
        return # 赔率无效跳过

    # 解析让球数
    handicap = 0
    goal_line = hhad.get("goalLine", "")
    if goal_line:
        try:
            handicap = int(float(goal_line))
        except (ValueError, TypeError):
            pass

    # 解析开球时间
    kickoff = None
    if match_date and match_time:
        try:
            kickoff = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M:%S")
            kickoff = kickoff.replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            pass

    # ─── 查找或创建球队 ───
    home_team = _get_or_create_team(db, home_cn, home_code, league, resolve_team_code, club_elo_ratings, result)
    away_team = _get_or_create_team(db, away_cn, away_code, league, resolve_team_code, club_elo_ratings, result)

    # ─── 生成比赛编码 ───
    match_code = _build_match_code(match_date, home_code, away_code, home_cn, away_cn)

    # ─── 查找或创建比赛 ───
    match = db.query(Match).filter(Match.match_code == match_code).first()
    is_new_match = match is None

    if is_new_match:
        match = Match(
            match_code=match_code,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            kickoff_at=kickoff,
            competition=league,
            match_type=MatchType.FRIENDLY,
            stage="group",
            status=MatchStatus.SCHEDULED,
            venue_type="home",
        )
        db.add(match)
        db.flush()
        result.matches_created += 1
    else:
        result.matches_updated += 1

    # ─── 写入赔率到 Match 表 ───
    odds_changed = _write_odds_to_match(match, odds_h, odds_d, odds_a, now, kickoff)
    if odds_changed:
        result.odds_written += 1

    # ─── 开盘价：首次出现赔率时记录 ───
    if match.opening_odds_home is None:
        match.opening_odds_home = odds_h
        match.opening_odds_draw = odds_d
        match.opening_odds_away = odds_a
        match.opening_odds_source = "sporttery"
        match.opening_odds_at = now
        result.opening_set += 1

    # ─── 收盘价：开球时间已过，设置为收盘 ───
    if kickoff and kickoff < now and match.status in (MatchStatus.SCHEDULED, MatchStatus.UPCOMING):
        match.closing_odds_home = odds_h
        match.closing_odds_draw = odds_d
        match.closing_odds_away = odds_a
        match.closing_odds_source = "sporttery"
        match.odds_locked_at = now
        result.closing_set += 1

    # ─── 创建/更新期号关联 ───
    _sync_issue_match(db, match, match_date, mdata, handicap, result)

    # ─── 记录赔率变动历史 ───
    if odds_changed:
        _record_odds_history(db, match, odds_h, odds_d, odds_a, now)

    # ─── 为新建比赛生成预测 ───
    if is_new_match and generate_predictions:
        try:
            _generate_prediction(db, match)
            result.predictions_generated += 1
        except Exception as e:
            logger.debug(f"[sporttery] 预测生成失败 {match_code}: {e}")


def _write_odds_to_match(
    match: Match,
    odds_h: float, odds_d: float, odds_a: float,
    now: datetime, kickoff: Optional[datetime],
) -> bool:
    """将赔率写入 Match 表，返回是否有变化"""
    changed = (
        match.odds_home != odds_h
        or match.odds_draw != odds_d
        or match.odds_away != odds_a
    )

    match.odds_home = odds_h
    match.odds_draw = odds_d
    match.odds_away = odds_a
    match.odds_source = "sporttery"

    # 如果开球时间已过且未锁定，标记为收盘价
    if kickoff and kickoff < now:
        match.closing_odds_home = odds_h
        match.closing_odds_draw = odds_d
        match.closing_odds_away = odds_a
        match.closing_odds_source = "sporttery"
        if not match.odds_locked_at:
            match.odds_locked_at = now

    return changed


def _sync_issue_match(
    db, match: Match, match_date: str,
    mdata: Dict[str, Any], handicap: int, result: SyncResult,
) -> None:
    """创建/更新 JingcaiIssue + JingcaiIssueMatch"""
    if not match_date:
        return

    issue_id = f"JC{match_date.replace('-', '')}"
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()

    if not issue:
        issue = JingcaiIssue(
            issue_id=issue_id,
            issue_type="spf14",
            status="on_sale",
        )
        db.add(issue)
        db.flush()
        result.issues_created += 1

    # 关联比赛到期号
    existing_link = db.query(JingcaiIssueMatch).filter(
        JingcaiIssueMatch.issue_id == issue.id,
        JingcaiIssueMatch.match_id == match.id,
    ).first()

    if existing_link:
        # 更新赔率数据
        existing_link.handicap = handicap
        if mdata.get("hhad"):
            existing_link.rq_odds = json.dumps(mdata["hhad"], ensure_ascii=False)
        if mdata.get("crs"):
            existing_link.score_odds = json.dumps(mdata["crs"], ensure_ascii=False)
        if mdata.get("ttg"):
            existing_link.goals_odds = json.dumps(mdata["ttg"], ensure_ascii=False)
        if mdata.get("hafu"):
            existing_link.half_odds = json.dumps(mdata["hafu"], ensure_ascii=False)
    else:
        seq = db.query(JingcaiIssueMatch).filter(
            JingcaiIssueMatch.issue_id == issue.id
        ).count() + 1

        link = JingcaiIssueMatch(
            issue_id=issue.id,
            match_id=match.id,
            sequence=seq,
            handicap=handicap,
            rq_odds=json.dumps(mdata.get("hhad", {}), ensure_ascii=False) if mdata.get("hhad") else None,
            score_odds=json.dumps(mdata.get("crs", {}), ensure_ascii=False) if mdata.get("crs") else None,
            goals_odds=json.dumps(mdata.get("ttg", {}), ensure_ascii=False) if mdata.get("ttg") else None,
            half_odds=json.dumps(mdata.get("hafu", {}), ensure_ascii=False) if mdata.get("hafu") else None,
        )
        db.add(link)
        db.flush()
        result.issue_matches_created += 1


def _record_odds_history(
    db, match: Match,
    odds_h: float, odds_d: float, odds_a: float,
    recorded_at: datetime,
) -> None:
    """记录赔率变动历史"""
    history = OddsHistory(
        match_id=match.id,
        source="sporttery",
        odds_home=odds_h,
        odds_draw=odds_d,
        odds_away=odds_a,
        recorded_at=recorded_at,
    )
    db.add(history)


def _generate_prediction(db, match: Match) -> None:
    """为比赛生成预测"""
    from prediction_engine import PredictionEngine, build_context_from_match
    ctx = build_context_from_match(match)
    engine = PredictionEngine(db_session=db)
    pred_result = engine.predict(ctx)

    from database.models import Prediction, PlayType
    for payload in pred_result.to_db_payload():
        existing = db.query(Prediction).filter(
            Prediction.match_id == match.id,
            Prediction.play_type == payload["play_type"],
        ).first()
        if not existing:
            pred = Prediction(
                match_id=match.id,
                play_type=payload["play_type"],
                probabilities=payload["probabilities"],
                confidence=payload.get("confidence"),
                model_version=payload.get("model_version", "v1.0"),
            )
            db.add(pred)
        else:
            existing.confidence = payload.get("confidence")
            existing.model_version = payload.get("model_version", "v1.0")


def _build_match_code(
    match_date: str, home_code: str, away_code: str,
    home_cn: str, away_cn: str,
) -> str:
    """生成比赛唯一编码"""
    if home_code and away_code:
        return f"JC-{match_date.replace('-', '')}-{home_code}-{away_code}"
    # 无 code 时用名称 hash
    h_hash = hashlib.md5(home_cn.encode()).hexdigest()[:6].upper()
    a_hash = hashlib.md5(away_cn.encode()).hexdigest()[:6].upper()
    return f"JC-{match_date.replace('-', '')}-{h_hash}-{a_hash}"


def _get_or_create_team(
    db, cn_name: str, code: str, league: str,
    resolve_team_code, club_elo_ratings: dict, result: SyncResult,
) -> Team:
    """根据中文名查找或创建球队（复用 jingcai_predictor 的逻辑）"""
    # 1. 尝试通过中文别名找到内置球队
    internal_code = resolve_team_code(cn_name)
    if internal_code:
        team = db.query(Team).filter(Team.code == internal_code).first()
        if team:
            return team
        # 创建内置球队
        if internal_code in club_elo_ratings:
            en_name, elo, lg = club_elo_ratings[internal_code]
            team = Team(
                code=internal_code,
                name=cn_name or en_name,
                name_en=en_name,
                elo=elo,
                fifa_rank=max(1, int((2100 - elo) / 10)),
                avg_goals_scored=1.4,
                avg_goals_conceded=1.2,
                tactical_style="balanced",
                continent="Europe",
            )
            db.add(team)
            db.flush()
            result.teams_created += 1
            return team

    # 2. 按中文名查找已有球队
    team = db.query(Team).filter(Team.name == cn_name).first()
    if team:
        return team

    # 3. 按 code 查找
    if code:
        team = db.query(Team).filter(Team.code == code).first()
        if team and team.name == cn_name:
            return team

    # 4. 创建新球队
    final_code = code or cn_name[:6].upper()
    existing = db.query(Team).filter(Team.code == final_code).first()
    if existing:
        final_code = f"{code}_{league[:3].upper()}" if code else cn_name[:8].upper()
        existing2 = db.query(Team).filter(Team.code == final_code).first()
        if existing2:
            suffix = hashlib.md5(cn_name.encode()).hexdigest()[:4].upper()
            final_code = f"{code or 'TM'}_{suffix}"

    team = Team(
        code=final_code,
        name=cn_name,
        name_en=code or cn_name,
        elo=1500,
        fifa_rank=50,
        avg_goals_scored=1.4,
        avg_goals_conceded=1.2,
        tactical_style="balanced",
        continent="Europe",
    )
    db.add(team)
    db.flush()
    result.teams_created += 1
    logger.info(f"[sporttery] Created team: {cn_name} ({final_code}, elo=1500)")
    return team


def _safe_float(val):
    """Convert val to float; return None for invalid values."""
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


# ────────────────────────────
# 历史数据回填
# ────────────────────────────

def backfill_historical_issues(days_back: int = 30) -> SyncResult:
    """
    回填历史期号赔率数据。

    sporttery.cn API 只返回当前在售的比赛，
    历史数据有限（大约能获取过去7天）。
    """
    result = SyncResult()
    src = JingcaiSource()
    db = SessionLocal()

    try:
        # 从过去7天到今天，逐日获取
        for i in range(days_back, 0, -1):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            d_next = (datetime.now() - timedelta(days=i - 1)).strftime("%Y-%m-%d")

            try:
                api_data = src._fetch_all_pools(d, d_next)
            except Exception as e:
                logger.debug(f"[sporttery] Backfill {d} failed: {e}")
                continue

            if not api_data:
                continue

            now = datetime.now(timezone.utc)
            resolve_team_code, club_elo_ratings, _ = _get_team_resolver()

            for mid, mdata in api_data.items():
                try:
                    # 不生成预测（历史数据回填）
                    _sync_single_match(
                        db=db,
                        mdata=mdata,
                        now=now,
                        result=result,
                        resolve_team_code=resolve_team_code,
                        club_elo_ratings=club_elo_ratings,
                        generate_predictions=False,
                    )
                except Exception as e:
                    result.errors.append(f"backfill {d} matchId={mid}: {e}")

            db.commit()
            logger.info(f"[sporttery] Backfill {d}: {len(api_data)} matches")

    except Exception as e:
        db.rollback()
        logger.error(f"[sporttery] Backfill error: {e}")
        result.errors.append(str(e))
    finally:
        db.close()
        src.close()

    return result


# ────────────────────────────
# 收盘价锁定
# ────────────────────────────

def lock_closing_odds() -> int:
    """
    对已开球但尚未锁定收盘价的比赛，用当前最新赔率锁定。

    比赛开球后，sporttery.cn API 仍会返回最终赔率一段时间，
    在下次同步时自动锁定为 closing_odds。
    此函数作为兜底：如果某场比赛已结束但未锁定收盘价，
    用 Match.odds_* 字段当前值作为收盘价。
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        unlocked = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.odds_home.isnot(None),
            Match.odds_home > 1.01,
            Match.closing_odds_home.is_(None),
        ).all()

        locked = 0
        for match in unlocked:
            match.closing_odds_home = match.odds_home
            match.closing_odds_draw = match.odds_draw
            match.closing_odds_away = match.odds_away
            match.closing_odds_source = match.odds_source or "sporttery"
            if not match.odds_locked_at:
                match.odds_locked_at = now
            locked += 1

        db.commit()
        if locked:
            logger.info(f"[sporttery] Locked closing odds for {locked} matches")
        return locked
    except Exception as e:
        db.rollback()
        logger.error(f"[sporttery] Lock closing odds error: {e}")
        return 0
    finally:
        db.close()


# ────────────────────────────
# 调度器入口
# ────────────────────────────

def sporttery_daily_sync_job() -> None:
    """调度器定时任务：每日同步 sporttery.cn 数据"""
    result = sync_from_sporttery(days_ahead=3, generate_predictions=True)
    logger.info(f"[sporttery] Daily sync: created={result.matches_created}, updated={result.matches_updated}, errors={len(result.errors)}")

    # 同步后尝试锁定收盘价
    lock_closing_odds()


def sporttery_odds_refresh_job() -> None:
    """调度器定时任务：每3小时刷新在售比赛赔率"""
    result = sync_from_sporttery(days_ahead=3, generate_predictions=True)
    logger.info(f"[sporttery] Odds refresh: updated={result.matches_updated}, odds_written={result.odds_written}")


def sporttery_backfill_job() -> None:
    """调度器定时任务：每周回填历史数据"""
    result = backfill_historical_issues(days_back=7)
    logger.info(f"[sporttery] Weekly backfill: created={result.matches_created}, updated={result.matches_updated}")
