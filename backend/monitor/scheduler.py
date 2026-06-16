"""
自动化调度中心 — APScheduler 配置
所有定时任务在此注册，支持启动/停止/查看日志
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
import logging
import os

from database.config import get_settings
settings = get_settings()
from database.models import SessionLocal, get_db, Match, MatchStatus, Prediction, Team

from utils.logger import get_logger

logger = get_logger("scheduler")
scheduler = BackgroundScheduler()


# ────────────────────────────
# 数据库会话上下文（修复 generator 泄漏）
# ────────────────────────────
class DBSession:
    """上下文管理器，确保调度器任务中的数据库会话正确关闭"""
    def __enter__(self):
        self.db = SessionLocal()
        return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.db.rollback()
        else:
            self.db.commit()
        self.db.close()
        return False


# ────────────────────────────
# Task 1a: Tier 1 — 基础数据检查（每2小时）
# football-data 缓存 + 本地数据新鲜度监控
# ────────────────────────────
# ────────────────────────────
# Task 0: Zgzcw — 中国足彩网百家欧赔采集（每30分钟）
# ────────────────────────────
def collect_zgzcw_job():
    """
    从中国足彩网（zgzcw.com）采集百家欧赔。
    一次采集覆盖 37 家公司，包括竞彩官方/澳门/香港马会。
    免费、无 API key、无请求限制。
    """
    from zgzcw_source import collect_zgzcw_odds

    with DBSession() as db:
        result = collect_zgzcw_odds(db)
        updated = result.get("updated", 0)
        total = result.get("matches", 0)
        if updated > 0:
            logger.info(f"[zgzcw] Updated {updated}/{total} matches with real odds from 37 companies")
        else:
            logger.debug(f"[zgzcw] No new odds (scanned {total} matches)")


def collect_500_job():
    """
    从 500.com 采集百家欧赔。
    每场覆盖 20+ 博彩公司，含竞彩官方/澳门/威廉希尔/bet365。
    免费、无 API key、无请求限制。
    """
    from wubaibai_source import collect_500_odds

    with DBSession() as db:
        result = collect_500_odds(db)
        updated = result.get("updated", 0)
        total = result.get("matches", 0)
        if updated > 0:
            logger.info(f"[500] Updated {updated}/{total} matches with real odds from 20+ bookmakers")
        else:
            logger.debug(f"[500] No new odds (scanned {total} matches)")


def collect_odds_tier1_job():
    """
    Tier 1: 免费/基础层
    - football-data 历史数据缓存更新（内部1小时缓存）
    - 检查本地赔率数据是否超过2小时未更新
    """
    from odds_collector import collect_odds_tier1_primary
    
    with DBSession() as db:
        result = collect_odds_tier1_primary(db)
        
        stale = result.get("stale_matches", 0)
        if stale > 0:
            logger.warning(f"[odds-tier1] {stale} matches have stale odds")
        logger.info(f"[odds-tier1] OK | budget: {result.get('budget_remaining', 'N/A')} credits remaining")


# ────────────────────────────
# Task 1b: Tier 2 — Odds API 全量采集（每天08:00, 20:00）
# ────────────────────────────
def collect_odds_tier2_job():
    """
    Tier 2: 付费全量层
    - 每天早晚各一次，用 Odds API 获取全部 upcoming 比赛赔率
    - 1 request = 1 credit
    """
    from odds_collector import collect_odds_tier2_premium
    
    with DBSession() as db:
        result = collect_odds_tier2_premium(db)
        
        if result.get("skipped"):
            logger.info(f"[odds-tier2] Skipped: {result.get('reason')}")
            return
        
        anomalies = result.get("anomalies", [])
        for a in anomalies:
            logger.warning(
                f"[odds-tier2] {a.match_id} | {a.source} | "
                f"{a.direction}: {a.old_odds:.2f} -> {a.new_odds:.2f} "
                f"({a.change_pct:+.1%}) [{a.severity}]"
            )
        
        logger.info(
            f"[odds-tier2] Fetched {result.get('matches_count', 0)} matches, "
            f"used {result.get('credits_used', 0)} credit, "
            f"remaining {result.get('budget_remaining', 0)}"
        )


# ────────────────────────────
# Task 1c: Tier 3 — 焦点战加采（每天12:00 + 赛前4h自动）
# ────────────────────────────
def collect_odds_tier3_job():
    """
    Tier 3: 焦点加采层
    - 每天中午对当天比赛额外采集
    - 赛前4小时内的比赛自动加采
    """
    from odds_collector import collect_odds_tier3_focus
    
    with DBSession() as db:
        result = collect_odds_tier3_focus(db)
        
        if result.get("skipped"):
            logger.info(f"[odds-tier3] Skipped: {result.get('reason')}")
            return
        
        anomalies = result.get("anomalies", [])
        for a in anomalies:
            logger.warning(
                f"[odds-tier3] {a.match_id} | {a.source} | "
                f"{a.direction}: {a.old_odds:.2f} -> {a.new_odds:.2f} "
                f"({a.change_pct:+.1%}) [{a.severity}]"
            )
        
        logger.info(
            f"[odds-tier3] Focus fetch: {result.get('matches_count', 0)} matches, "
            f"used {result.get('credits_used', 0)} credit, "
            f"remaining {result.get('budget_remaining', 0)}"
        )


# ────────────────────────────
# Task 1d: 收盘赔率采集（赛前90分钟内，每15分钟）
# ────────────────────────────
def collect_closing_odds_job():
    """
    赛前 90 分钟内采集真实收盘赔率。
    这是消除市场模型循环引用的关键：只存真实源， synthetic 不参与。
    """
    from odds_collector import collect_closing_odds_for_upcoming

    with DBSession() as db:
        result = collect_closing_odds_for_upcoming(db, hours=4)
        if result.get("skipped"):
            logger.debug(f"[closing-odds] Skipped: {result.get('reason')}")
        else:
            logger.info(
                f"[closing-odds] Updated {result.get('matches_updated', 0)}/"
                f"{result.get('matches_processed', 0)} matches"
            )


def update_opening_odds_job():
    """
    批量更新开盘赔率：从 OddsHistory 找出最早真实快照，
    写入 Match.opening_odds_* 字段。
    """
    from odds_tracker import OddsTracker

    with DBSession() as db:
        tracker = OddsTracker(db)
        updated = tracker.batch_update_opening_odds()
        if updated > 0:
            logger.info(f"[opening-odds] Updated {updated} matches")


def live_odds_poll_job():
    """
    滚球赔率采集。调用 LiveOddsFeed.poll_once()。
    仅在有待采集的 live/scheduled 比赛时执行。
    """
    from live_odds_feed import LiveOddsFeed, get_odds_bus

    feed = LiveOddsFeed(
        bus=get_odds_bus(),
        poll_interval=settings.LIVE_ODDS_POLL_INTERVAL,
        use_simulated=True,
    )
    n = feed.poll_once()
    if n > 0:
        logger.info(f"[live-odds] {n} updates collected")


# ────────────────────────────
# Task 2: 赛前预测锁定（赛前48h / 赛前2h）
# ────────────────────────────
def lock_predictions_job():
    """
    对48h内即将开始的比赛，自动运行预测模型并锁定快照。
    优先使用已采集的收盘赔率（closing_odds），确保市场信号独立于 Elo。
    """
    now = datetime.now(timezone.utc)
    window = now + timedelta(hours=48)

    with DBSession() as db:
        matches = db.query(Match).filter(
            Match.kickoff_at <= window,
            Match.kickoff_at > now,
            Match.status == MatchStatus.SCHEDULED
        ).all()

        for match in matches:
            # 检查是否已锁定
            existing = db.query(Prediction).filter(Prediction.match_id == match.id).first()
            if existing:
                continue

            logger.info(f"[prediction] Locking predictions for {match.match_code}")

            # 调用预测引擎（使用 closing_odds 作为市场输入，消除循环引用）
            try:
                from core.prediction_engine import PredictionEngine, build_context_from_match
                ctx = build_context_from_match(match)
                engine = PredictionEngine(db_session=db)
                result = engine.predict(ctx)
                for payload in result.to_db_payload():
                    pred = Prediction(
                        match_id=match.id,
                        play_type=payload["play_type"],
                        probabilities=payload["probabilities"],
                        confidence=payload.get("confidence"),
                        model_version=payload.get("model_version", "v1.0"),
                    )
                    db.add(pred)
                logger.info(
                    f"[prediction] {match.match_code} locked | "
                    f"SPF: H={result.spf.get('home', 0):.2%} D={result.spf.get('draw', 0):.2%} A={result.spf.get('away', 0):.2%}"
                )
            except Exception as e:
                logger.error(f"[prediction] Failed to lock {match.match_code}: {e}")

            # 更新比赛状态
            match.status = MatchStatus.UPCOMING
            db.commit()


# ────────────────────────────
# Task 3: 比赛状态监控（每分钟）
# ────────────────────────────
def match_monitor_job():
    """
    检查是否有比赛已开始或已结束
    - 检测到开球 → 状态改为 LIVE，锁定所有预测（不再更新）
    - 检测到结束 → 状态改为 FINISHED，触发结果录入提醒
    """
    now = datetime.now(timezone.utc)
    with DBSession() as db:
        # 检测即将开始的比赛（5分钟内）
        starting = db.query(Match).filter(
            Match.kickoff_at <= now + timedelta(minutes=5),
            Match.kickoff_at > now - timedelta(minutes=5),
            Match.status == MatchStatus.UPCOMING
        ).all()
        
        for match in starting:
            logger.info(f"[monitor] Match {match.match_code} is starting")
            match.status = MatchStatus.LIVE
            db.commit()
        
        # 检测可能已结束的比赛（开球后>105分钟）
        ended = db.query(Match).filter(
            Match.status == MatchStatus.LIVE,
            Match.kickoff_at < now - timedelta(minutes=105)
        ).all()
        
        for match in ended:
            # 调低日志级别，避免淹没控制台
            logger.debug(f"[monitor] Match {match.match_code} likely ended, awaiting result input")
            # 不自动改状态，因为需要人工/自动确认比分
            # 可以发通知提醒管理员录入结果


# ────────────────────────────
# ───────────────────────────
# ───────────────────────────
# Task 4: 自动结果同步（每5分钟）
# 数据源: openfootball/football.json (免费、无API Key)
# ───────────────────────────

_OPENFOOTBALL_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
_OPENFOOTBALL_SEASON = "2025-26"
_OPENFOOTBALL_LEAGUES = {
    "en.1": "EPL", "de.1": "Bundesliga", "es.1": "LaLiga",
    "it.1": "SerieA", "fr.1": "Ligue1",
    "en.2": "Championship", "de.2": "Bundesliga2",
    "es.2": "LaLiga2", "it.2": "SerieB", "fr.2": "Ligue2",
}
_OPENFOOTBALL_LOCAL = os.getenv("OPENFOOTBALL_LOCAL_DIR", "")  # 本地数据目录优先

import ssl as _ssl
try:
    import certifi
    _ctx = _ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ctx = _ssl.create_default_context()


def _fetch_openfootball_json(league_code: str, season: str = _OPENFOOTBALL_SEASON):
    """从 openfootball 获取赛季数据"""
    import urllib.request, json as _json
    # 本地优先
    if _OPENFOOTBALL_LOCAL:
        local_path = os.path.join(_OPENFOOTBALL_LOCAL, season, f"{league_code}.json")
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    return _json.load(f)
            except Exception as e:
                logger.warning(f"[result] Local file error: {e}")
    # 在线获取
    url = f"{_OPENFOOTBALL_BASE}/{season}/{league_code}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WC-Analytics/1.0"})
        with urllib.request.urlopen(req, timeout=30, context=_ctx) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"[result] openfootball fetch error: {e}")
        return None


def sync_results_job():
    """从 openfootball/football.json 同步已结束比赛的结果"""
    # 先用 result_sync 模块（覆盖五大联赛 SCHEDULED + LIVE + UPCOMING）
    try:
        from result_sync import sync_results
        with DBSession() as db:
            count = sync_results(db, days_back=14)
            if count:
                logger.info(f"[result] result_sync 模块同步 {count} 场")
                return
    except Exception as e:
        logger.warning(f"[result] result_sync 模块失败: {e}")

    # 退回旧逻辑（仅匹配 LIVE/UPCOMING）
    from openfootball_importer import TeamMatcher

    with DBSession() as db:
        live_matches = db.query(Match).filter(
            Match.status.in_([MatchStatus.LIVE, MatchStatus.UPCOMING]),
            Match.kickoff_at < datetime.now(timezone.utc) - timedelta(minutes=90),
        ).all()

        if not live_matches:
            return

        # 修复：如果不是 sqlite，不要尝试传递路径
        db_url = str(db.bind.url)
        db_path = db_url.replace("sqlite:///", "") if "sqlite" in db_url else None
        matcher = TeamMatcher(db_path or "database.sqlite")
        synced = 0

        for league_code, comp_name in _OPENFOOTBALL_LEAGUES.items():
            comp_matches = [m for m in live_matches if m.competition == comp_name]
            if not comp_matches:
                continue

            data = _fetch_openfootball_json(league_code)
            if not data:
                continue

            for m in data.get("matches", []):
                score = m.get("score", {})
                if isinstance(score, list):
                    # Fallback if score is just [hg, ag]
                    ft = score
                elif isinstance(score, dict):
                    ft = score.get("ft", [])
                else:
                    continue
                
                if not isinstance(ft, list) or len(ft) != 2:
                    continue
                try:
                    hg, ag = int(ft[0]), int(ft[1])
                except (ValueError, TypeError):
                    continue

                team1 = m.get("team1", "")
                team2 = m.get("team2", "")
                home_team_match = matcher.match(team1, auto_create=False)
                away_team_match = matcher.match(team2, auto_create=False)
                if not home_team_match or not away_team_match:
                    continue

                for match in comp_matches:
                    if (match.home_team_id == home_team_match["id"]
                            and match.away_team_id == away_team_match["id"]
                            and match.status != MatchStatus.FINISHED):
                        match.actual_home_goals = hg
                        match.actual_away_goals = ag
                        match.actual_outcome = "home" if hg > ag else ("draw" if hg == ag else "away")
                        match.status = MatchStatus.FINISHED
                        db.commit()
                        synced += 1
                        ht_name = db.query(Team).filter(Team.id == match.home_team_id).first()
                        at_name = db.query(Team).filter(Team.id == match.away_team_id).first()
                        logger.info(
                            f"[result] Synced: {match.match_code} "
                            f"{ht_name.name if ht_name else '?'} {hg}-{ag} {at_name.name if at_name else '?'}"
                        )
                        break

        if synced:
            logger.info(f"[result] Synced {synced} results via openfootball")
        else:
            logger.debug(f"[result] No new results for {len(live_matches)} live matches")


# ────────────────────────────
# Task 5: 数据库备份（每日凌晨）
# ────────────────────────────
def backup_database_job(
    backup_dir: str = None,
    db_path: str = None,
    keep_daily: int = 7,
    keep_weekly: int = 4,
    max_size_gb: float = 5.0,
):
    """
    每日备份 SQLite 数据库到 backup/ 目录。

    修复记录 (2026-06-16):
      - P0 修复: 加入保留策略,避免 backup/ 无限累积
                (5-27 一天生成 11 个,长期累积 40 个 ~2.1GB)
      - P1 修复: 路径从相对路径改为可注入参数,兼容 systemd / cron
      - P1 修复: 修复 'db' 未定义的 finally bug (原代码 src.close()/dst.close())
      - P2 优化: 同一天内若 db 哈希未变,跳过备份
      - P2 优化: 每周日的备份额外保留 4 周作为周备

    保留策略:
      - 日备: 最近 keep_daily=7 天
      - 周备: 最近 keep_weekly=4 个周日备份
      - 总大小上限: max_size_gb GB,超出时按 mtime 删最旧
    """
    import os
    import glob
    import sqlite3
    import hashlib
    import re

    # 💡 动态解析绝对路径，防止 CWD 漂移导致找不到数据库
    try:
        from database.config import get_settings, _BACKEND_ROOT
        settings = get_settings()
    except Exception:
        settings = None
        _BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if db_path is None:
        if settings and settings.DATABASE_URL.startswith("sqlite:///"):
            db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        else:
            db_path = os.path.join(_BACKEND_ROOT, "database.sqlite")
    else:
        # 如果传入了相对路径，尝试基于 backend 根定位
        if not os.path.isabs(db_path):
            abs_candidate = os.path.abspath(os.path.join(_BACKEND_ROOT, db_path.replace("./", "")))
            if os.path.exists(abs_candidate) or not os.path.exists(db_path):
                db_path = abs_candidate

    if backup_dir is None:
        backup_dir = os.path.join(_BACKEND_ROOT, "backup")
    else:
        if not os.path.isabs(backup_dir):
            backup_dir = os.path.abspath(os.path.join(_BACKEND_ROOT, backup_dir.replace("./", "")))

    os.makedirs(backup_dir, exist_ok=True)

    if not os.path.exists(db_path):
        logger.warning(f"[backup] DB not found, skip: {db_path}")
        return {"status": "skipped", "reason": "db_missing"}

    # 计算 db 哈希,与上次备份对比,无变化则跳过
    try:
        with open(db_path, "rb") as f:
            db_hash = hashlib.md5(f.read()).hexdigest()[:12]
    except OSError as e:
        logger.error(f"[backup] Failed to hash db: {e}")
        return {"status": "failed", "reason": str(e)}

    # 读最近一次备份的元数据
    meta_path = os.path.join(backup_dir, ".backup_meta.json")
    last_hash = None
    if os.path.exists(meta_path):
        try:
            import json
            with open(meta_path, "r") as f:
                meta = json.load(f)
                last_hash = meta.get("last_hash")
        except Exception:
            pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"db_{timestamp}.sqlite")

    if db_hash == last_hash:
        logger.info(f"[backup] DB unchanged (hash={db_hash}), skip")
        return {"status": "skipped", "reason": "unchanged", "hash": db_hash}

    # 执行备份 (使用 SQLite backup API 确保 WAL 模式一致性)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        size_mb = os.path.getsize(backup_path) / 1024 / 1024
        logger.info(f"[backup] OK -> {backup_path} ({size_mb:.1f}MB, hash={db_hash})")
    except Exception as e:
        logger.error(f"[backup] Backup failed: {e}")
        if os.path.exists(backup_path):
            os.remove(backup_path)
        return {"status": "failed", "reason": str(e)}
    finally:
        src.close()
        dst.close()

    # 写元数据
    try:
        import json
        with open(meta_path, "w") as f:
            json.dump(
                {
                    "last_hash": db_hash,
                    "last_backup": timestamp,
                    "last_size_mb": round(size_mb, 2),
                },
                f,
            )
    except OSError:
        pass

    # ── 保留策略清理 ──
    cleanup_old_backups(backup_dir, keep_daily, keep_weekly, max_size_gb)
    return {"status": "ok", "path": backup_path, "size_mb": round(size_mb, 2)}


def cleanup_old_backups(
    backup_dir: str,
    keep_daily: int = 7,
    keep_weekly: int = 4,
    max_size_gb: float = 5.0,
):
    """
    按保留策略清理旧备份。

    策略:
      1. 日备: 保留最近 keep_daily=7 个连续日备份
      2. 周备: 保留最近 keep_weekly=4 个周日备份
      3. 大小: 若总大小 > max_size_gb GB, 按 mtime 删除最旧的,直到达标
    """
    import os
    import re
    from datetime import datetime, timedelta

    pattern = re.compile(r"db_(\d{8})_(\d{6})\.sqlite$")
    files = []
    for f in os.listdir(backup_dir):
        m = pattern.match(f)
        if not m:
            continue
        path = os.path.join(backup_dir, f)
        if not os.path.isfile(path):
            continue
        try:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        files.append((ts, path, os.path.getsize(path)))

    files.sort(reverse=True)  # 最新优先
    to_keep = set()

    # 1) 日备: 连续 keep_daily 天,每天保留 1 个
    seen_dates = set()
    daily_count = 0
    for ts, path, _ in files:  # 新 → 旧
        d = ts.date()
        if d in seen_dates:
            continue
        seen_dates.add(d)
        if daily_count >= keep_daily:
            break
        to_keep.add(path)
        daily_count += 1

    # 2) 周备: 周日的备份额外保留 keep_weekly 周
    seen_weeks = set()
    weekly_count = 0
    for ts, path, _ in files:
        if ts.weekday() != 6:  # 6 = Sunday
            continue
        iso_week = ts.isocalendar()[:2]
        if iso_week in seen_weeks:
            continue
        seen_weeks.add(iso_week)
        if weekly_count >= keep_weekly:
            break
        to_keep.add(path)
        weekly_count += 1

    # 3) 大小: 超过 max_size_gb 删最旧 (强制，最新优先保留)
    total_size = sum(s for _, _, s in files)
    max_bytes = int(max_size_gb * 1024 ** 3)
    for ts, path, size in reversed(files):  # 旧 → 新
        if total_size <= max_bytes:
            break
        try:
            os.remove(path)
            total_size -= size
            if path in to_keep:
                to_keep.remove(path)
            logger.info(f"[backup] cleanup: removed {os.path.basename(path)} (size cap)")
        except OSError as e:
            logger.warning(f"[backup] cleanup failed: {e}")

    # 4) 删除不在 to_keep 集合的
    removed = 0
    for ts, path, size in files:
        if path in to_keep:
            continue
        if not os.path.exists(path):  # 避免已被容量限制删除了的文件报错
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            logger.warning(f"[backup] cleanup failed: {e}")

    if removed:
        logger.info(f"[backup] cleanup: removed {removed} old files, keep {len(to_keep)}")


# ────────────────────────────
# 开奖结果自动录入
# ────────────────────────────

def zgzcw_draw_sync_job():
    """从中国足彩网（zgzcw.com）采集开奖结果并更新比赛状态。

    每 6 小时运行一次，检查最近 14 天的待更新比赛。
    """
    from zgzcw_draw import sync_recent_draw
    result = sync_recent_draw(days_back=14)
    updated = result.get("updated", 0)
    matched = result.get("matched", 0)
    autodrawn = result.get("autodrawn", 0)
    filled = result.get("filled", 0)
    if updated or autodrawn:
        logger.info(
            f"[scheduler] zgzcw draw sync: {matched} matched, "
            f"{updated} updated, {autodrawn} auto-drawn, {filled} filled"
        )
    else:
        logger.debug(f"[scheduler] zgzcw draw sync: {matched} matched, no new updates")


def fill_drawn_issues_job():
    """自动补录已开奖竞彩期号的开奖结果

    每 5 分钟检查一次，所有 drawn 期号的比赛产出 actual_outcome
    后自动填充 draw_result + verification。
    """
    with DBSession() as db:
        from sqlalchemy import text

        drawn_issues = db.query(JingcaiIssue).filter(
            JingcaiIssue.status == 'drawn',
            JingcaiIssue.draw_result.is_(None),
        ).all()

        if not drawn_issues:
            return

        filled = 0
        for iss in drawn_issues:
            rows = db.execute(text('''
                SELECT m.match_code, m.actual_outcome
                FROM jingcai_issue_matches jim
                JOIN matches m ON m.id = jim.match_id
                WHERE jim.issue_id = :iid
                ORDER BY jim.sequence
            '''), {'iid': iss.id}).fetchall()

            results = []
            all_known = True
            for r in rows:
                outcome = r[1]
                if not outcome:
                    all_known = False
                results.append(outcome if outcome else "unknown")

            if not all_known:
                continue

            total = len(results)
            known = sum(1 for r in results if r != "unknown")
            home_wins = sum(1 for r in results if r == "home")
            draws = sum(1 for r in results if r == "draw")
            away_wins = sum(1 for r in results if r == "away")

            import json
            iss.draw_result = json.dumps({
                "results": results,
                "prizes": {},
            }, ensure_ascii=False)
            iss.verification = json.dumps({
                "total_matches": total,
                "known_results": known,
                "home_wins": home_wins,
                "draws": draws,
                "away_wins": away_wins,
            }, ensure_ascii=False)
            filled += 1
            logger.info(f"[draw] {iss.issue_id}: {known}/{total} results auto-filled (H={home_wins} D={draws} A={away_wins})")

        if filled:
            db.commit()
            logger.info(f"[draw] {filled} issues auto-filled")


# ────────────────────────────
# Task 5: 数据库备份（每日凌晨）
# ────────────────────────────
# Task 5b: FBref 球队+球员统计同步（每周日 04:00）
# ────────────────────────────
def collect_fbref_stats_job():
    """
    每周同步 FBref 高级统计到数据库。
    - 球队 xG / possession / shots 等 → teams 表
    - 球员 xG / xA / minutes 等 → player_stats 表
    """
    try:
        from integrations.soccerdata_adapter import SoccerDataSync
        with DBSession() as db:
            sync = SoccerDataSync(db)
            # 同步国家队数据（世界杯/欧洲杯）
            team_cnt = sync.sync_fbref_team_stats("INT-World Cup", "2022")
            player_cnt = sync.sync_fbref_player_stats("INT-World Cup", "2022")
            logger.info(
                f"[fbref-sync] Weekly sync done: teams={team_cnt}, players={player_cnt}"
            )
    except Exception as e:
        logger.error(f"[fbref-sync] Weekly sync failed: {e}")


# ────────────────────────────
# Task 5c: Club Elo 等级分同步（每周日 04:30）
# ────────────────────────────
def collect_elo_ratings_job():
    """
    每周同步 Club Elo 等级分到 teams 表。
    用于校准 EloModel 的基准输入，消除手工估算误差。
    """
    try:
        from integrations.soccerdata_adapter import SoccerDataSync
        with DBSession() as db:
            sync = SoccerDataSync(db)
            updated = sync.sync_elo_ratings()
            logger.info(f"[elo-sync] Weekly sync done: updated={updated}")
    except Exception as e:
        logger.error(f"[elo-sync] Weekly sync failed: {e}")


# ────────────────────────────
# Task 6: 近期状态采集（每天 06:00）
# football-data.org 免费 tier 100 calls/day，94 支球队约 94 次调用
# ────────────────────────────
def collect_form_job():
    """
    每天自动刷新所有球队的近期战绩。
    优先内部数据库，不足时调用 football-data.org API 补充。
    """
    from form_collector import FormCollector

    with DBSession() as db:
        try:
            collector = FormCollector(db)
            stats = collector.refresh_all(use_external=True)
            logger.info(
                f"[form] Daily refresh done: updated={stats['updated']}, "
                f"skipped={stats['skipped']}, failed={stats['failed']}"
            )
        except Exception as e:
            logger.error(f"[form] Daily refresh failed: {e}")


# ────────────────────────────
# Task 7: 准确率自动计算（每小时）
# ────────────────────────────
def calculate_accuracy_job():
    """
    对已结束且已录入预测的比赛，自动计算各玩法准确率
    结果存入统计表（可扩展）
    """
    with DBSession() as db:
        finished = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None)
        ).all()
        
        total_checked = 0
        correct = 0
        
        for match in finished:
            preds = db.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == "SPF"
            ).all()
            
            for pred in preds:
                total_checked += 1
                probs = pred.probabilities
                predicted = max(probs, key=probs.get)
                if predicted == match.actual_outcome:
                    correct += 1
        
        if total_checked > 0:
            accuracy = correct / total_checked
            logger.info(f"[accuracy] SPF accuracy: {accuracy:.2%} ({correct}/{total_checked})")


# ────────────────────────────
# 足彩期号自动验证 — 检查已开奖但未验证的期号并执行verify
# ────────────────────────────
def jingcai_auto_verify_wrapper():
    from database.models import get_db, JingcaiIssue
    from jingcai_predictor import verify_issue
    db = next(get_db())
    try:
        # 先尝试同步结果，让 closed 期号中的比赛获得结果
        try:
            from result_sync import sync_results
            synced = sync_results(db, days_back=14)
            if synced:
                logger.info(f"[jingcai-verify] 预同步 {synced} 场结果")
        except Exception as e:
            logger.warning(f"[jingcai-verify] 预同步失败: {e}")

        # 自动关闭所有已过期但未关闭的期号
        try:
            from api.routers.jingcai import _auto_close_expired_issues
            closed = _auto_close_expired_issues(db)
            if closed:
                logger.info(f"[jingcai-verify] 自动关闭 {closed} 个过期期号")
        except Exception as e:
            logger.warning(f"[jingcai-verify] 自动关闭失败: {e}")

        drawn_issues = db.query(JingcaiIssue).filter(
            JingcaiIssue.status.in_(["drawn", "closed"]),
            JingcaiIssue.verification == None,
        ).all()
        verified_count = 0
        for issue in drawn_issues:
            if not issue.draw_result:
                continue
            try:
                verify_issue(db, issue.issue_id)
                verified_count += 1
                logger.info(f"[jingcai-verify] Verified issue {issue.issue_id}")
            except Exception as e:
                logger.warning(f"[jingcai-verify] Failed for {issue.issue_id}: {e}")
        logger.info(f"[jingcai-verify] Auto-verified {verified_count} issues")
    finally:
        db.close()


# ────────────────────────────
# 注册所有任务
# ────────────────────────────
def start_scheduler():
    """启动所有定时任务"""
    
    # Tier 0: Zgzcw 百家欧赔采集 — 每30分钟
    scheduler.add_job(
        collect_zgzcw_job,
        trigger=IntervalTrigger(minutes=30),
        id="collect_zgzcw",
        name="Zgzcw Odds Collection (37 companies, free, CN)",
        replace_existing=True
    )

    # Tier 0b: 500.com 百家欧赔采集 — 每30分钟
    scheduler.add_job(
        collect_500_job,
        trigger=IntervalTrigger(minutes=30),
        id="collect_500",
        name="500.com Odds Collection (20+ companies, free, CN)",
        replace_existing=True
    )

    # Tier 1: 基础检查 — 每2小时
    scheduler.add_job(
        collect_odds_tier1_job,
        trigger=IntervalTrigger(hours=2),
        id="collect_odds_tier1",
        name="Odds Collection Tier 1 (Primary)",
        replace_existing=True
    )
    
    # Tier 2: Odds API 全量采集 — 每天08:00和20:00
    scheduler.add_job(
        collect_odds_tier2_job,
        trigger=CronTrigger(hour="8,20", minute=0),
        id="collect_odds_tier2",
        name="Odds Collection Tier 2 (Premium)",
        replace_existing=True
    )
    
    # Tier 3: 焦点战加采 — 每天12:00
    scheduler.add_job(
        collect_odds_tier3_job,
        trigger=CronTrigger(hour=12, minute=0),
        id="collect_odds_tier3",
        name="Odds Collection Tier 3 (Focus)",
        replace_existing=True
    )
    
    # Tier 3 自动触发：每小时检查是否有比赛在4h内开始
    def auto_focus_trigger():
        now = datetime.now(timezone.utc)
        with DBSession() as db:
            focus_matches = db.query(Match).filter(
                Match.kickoff_at.between(now, now + timedelta(hours=4)),
                Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING])
            ).all()
            if focus_matches:
                logger.info(f"[auto-focus] {len(focus_matches)} matches within 4h, triggering tier3")
                collect_odds_tier3_job()
    
    scheduler.add_job(
        auto_focus_trigger,
        trigger=IntervalTrigger(hours=1),
        id="auto_focus_trigger",
        name="Auto Focus Trigger (4h pre-match)",
        replace_existing=True
    )
    
    # 收盘赔率采集：每15分钟（赛前90分钟内比赛）
    scheduler.add_job(
        collect_closing_odds_job,
        trigger=IntervalTrigger(minutes=15),
        id="collect_closing_odds",
        name="Closing Odds Collection (real market only)",
        replace_existing=True
    )

    # 预测锁定：每小时检查一次
    scheduler.add_job(
        lock_predictions_job,
        trigger=IntervalTrigger(hours=1),
        id="lock_predictions",
        name="Prediction Lock",
        replace_existing=True
    )
    
    # 比赛监控：每分钟
    scheduler.add_job(
        match_monitor_job,
        trigger=IntervalTrigger(minutes=1),
        id="match_monitor",
        name="Match Monitor",
        replace_existing=True
    )
    
    # 结果同步：每5分钟
    scheduler.add_job(
        sync_results_job,
        trigger=IntervalTrigger(minutes=5),
        id="sync_results",
        name="Result Sync",
        replace_existing=True
    )

    # 开奖结果自动录入：每 6 小时检查一次 drawn 期号
    scheduler.add_job(
        fill_drawn_issues_job,
        trigger=IntervalTrigger(hours=6),
        id="fill_drawn_issues",
        name="Jingcai Draw Result Auto-Fill",
        replace_existing=True
    )
    
    # 数据库备份：每日凌晨3点
    scheduler.add_job(
        backup_database_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="backup_db",
        name="Database Backup",
        replace_existing=True
    )
    
    # FBref 高级统计同步：每周日 04:00
    scheduler.add_job(
        collect_fbref_stats_job,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="collect_fbref_stats",
        name="FBref Team & Player Stats Sync",
        replace_existing=True
    )

    # Club Elo 等级分同步：每周日 04:30
    scheduler.add_job(
        collect_elo_ratings_job,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=30),
        id="collect_elo_ratings",
        name="Club Elo Ratings Sync",
        replace_existing=True
    )

    # 近期状态采集：每天 06:00
    scheduler.add_job(
        collect_form_job,
        trigger=CronTrigger(hour=6, minute=0),
        id="collect_form",
        name="Team Form Collection (football-data.org)",
        replace_existing=True
    )

    # xG 估算补充：每天 05:00（在 form 采集前运行）
    def fill_xg_job():
        """为缺失 xG/xGA 的球队填充估算值"""
        from xg_estimator import fill_missing_xg
        with DBSession() as db:
            try:
                stats = fill_missing_xg(db)
                logger.info(
                    f"[xg-fill] xG estimation done: skipped={stats['skipped']}, "
                    f"from_matches={stats['from_matches']}, from_elo={stats['from_elo']}, "
                    f"from_default={stats['from_default']}, errors={stats['errors']}"
                )
            except Exception as e:
                logger.error(f"[xg-fill] xG estimation failed: {e}")


    scheduler.add_job(
        fill_xg_job,
        trigger=CronTrigger(hour=5, minute=0),
        id="fill_xg",
        name="xG Estimation (Elo regression)",
        replace_existing=True
    )

    # 准确率计算：每小时
    scheduler.add_job(
        calculate_accuracy_job,
        trigger=IntervalTrigger(hours=1),
        id="calc_accuracy",
        name="Accuracy Calculation",
        replace_existing=True
    )

    # 竞彩期号同步：每天 09:00, 15:00
    def jingcai_sync_job():
        """自动同步在售赛事期号"""
        from jingcai_predictor import cmd_issue_sync
        try:
            cmd_issue_sync(days=3)
            logger.info("[jingcai-sync] Daily sync done")
        except Exception as e:
            logger.error(f"[jingcai-sync] Daily sync failed: {e}")
            from alert_manager import fire_alert
            fire_alert("jingcai_sync", "critical", f"竞彩期号同步失败: {e}")

    scheduler.add_job(
        jingcai_sync_job,
        trigger=CronTrigger(hour="9,15", minute=0),
        id="jingcai_sync",
        name="Jingcai Issue Sync (sporttery API)",
        replace_existing=True
    )

    # 自检+自修引擎：每10分钟
    def health_check_wrapper():
        from health_daemon import health_check_job
        health_check_job()

    scheduler.add_job(
        health_check_wrapper,
        trigger=IntervalTrigger(minutes=10),
        id="health_check",
        name="Health Daemon (self-check + self-repair)",
        replace_existing=True,
    )

    # 模型每日复盘：每天 05:30（在 xG 估算和 form 采集之后）
    def daily_audit_wrapper():
        from model_audit import daily_audit_job
        daily_audit_job()

    scheduler.add_job(
        daily_audit_wrapper,
        trigger=CronTrigger(hour=5, minute=30),
        id="daily_audit",
        name="Model Daily Audit (prediction vs result)",
        replace_existing=True,
    )

    # 模型每周深度复盘：每周一 06:00
    def weekly_audit_wrapper():
        from model_audit import weekly_audit_job
        weekly_audit_job()

    scheduler.add_job(
        weekly_audit_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="weekly_audit",
        name="Model Weekly Deep Audit + Self-Heal",
        replace_existing=True,
    )




    # 自愈闭环：审计→重学→重生成：每周一 06:15
    def self_heal_wrapper():
        from model_audit import self_heal_job
        result = self_heal_job()
        status = result.get("status", "unknown")
        dur = result.get("duration_seconds", 0)
        logger.info(f"[self-heal-scheduled] status={status}, duration={dur:.0f}s")

    scheduler.add_job(
        self_heal_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=15),
        id="self_heal_cycle",
        name="Self-Heal Cycle: Audit -> Reweight -> Regenerate",
        replace_existing=True,
    )

    # 预测神经网络每日训练：每天 06:30
    # ── 神经网络自学任务 (MLOps) ──
    def core_nn_train_wrapper():
        from core.residual_nn import StackingTrainer
        from database.models import SessionLocal
        with SessionLocal() as db:
            trainer = StackingTrainer(db_session=db)
            result = trainer.train()
            logger.info(f"[MLOps] Stacking NN Training result: {result}")

    scheduler.add_job(
        core_nn_train_wrapper,
        trigger=CronTrigger(hour=4, minute=0),
        id="core_nn_train",
        name="Core Stacking NN Training (Daily)",
        replace_existing=True,
    )

    # ── 平局分类器每日训练：每天 06:35 ──
    def draw_classifier_train_wrapper():
        from draw_classifier import draw_classifier_train_job
        draw_classifier_train_job()

    scheduler.add_job(
        draw_classifier_train_wrapper,
        trigger=CronTrigger(hour=6, minute=35),
        id="draw_classifier_train",
        name="Draw Classifier Daily Training",
        replace_existing=True,
    )

    # ── 子模型每周增量训练 ──
    def halftime_train_wrapper():
        from sub_model_halftime import halftime_train_job
        halftime_train_job()

    def score_train_wrapper():
        from sub_model_score import score_train_job
        score_train_job()

    def handicap_train_wrapper():
        from sub_model_handicap import handicap_train_job
        handicap_train_job()

    # 半场子模型：每周一 06:45
    scheduler.add_job(
        halftime_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=45),
        id="halftime_train",
        name="Halftime Sub-model Weekly Training",
        replace_existing=True,
    )

    # 比分子模型：每周一 06:50
    scheduler.add_job(
        score_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=50),
        id="score_train",
        name="Score Sub-model Weekly Training",
        replace_existing=True,
    )

    # 让球子模型：每周一 06:55
    scheduler.add_job(
        handicap_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=55),
        id="handicap_train",
        name="Handicap Sub-model Weekly Training",
        replace_existing=True,
    )

    # ────────────────────────────
    # Task: zgzcw 数据同步（主力数据源，替代 sporttery.cn）
    # ────────────────────────────
    def zgzcw_daily_sync_wrapper():
        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches()
        logger.info(f"[zgzcw-daily-sync] Sync result: {result}")

    def zgzcw_odds_refresh_wrapper():
        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches()
        logger.info(f"[zgzcw-odds-refresh] Sync result: {result}")

    # 每日同步 zgzcw 竞彩比赛 + 期号关联 + 赔率历史：每天 08:00
    scheduler.add_job(
        zgzcw_daily_sync_wrapper,
        trigger=CronTrigger(hour=8, minute=0),
        id="zgzcw_daily_sync",
        name="Zgzcw Daily Match + Issue Sync",
        replace_existing=True,
    )

    # 每3小时刷新赔率
    scheduler.add_job(
        zgzcw_odds_refresh_wrapper,
        trigger=CronTrigger(hour="*/3", minute=17),
        id="zgzcw_odds_refresh",
        name="Zgzcw Odds Refresh (3h)",
        replace_existing=True,
    )

    # Zgzcw 开奖结果采集：每 6 小时
    scheduler.add_job(
        zgzcw_draw_sync_job,
        trigger=IntervalTrigger(hours=6),
        id="zgzcw_draw_sync",
        name="Zgzcw Draw Result Sync (6h)",
        replace_existing=True,
    )

    # 竞彩自动核验（drawn → verification）：每 6 小时
    def auto_verify_wrapper():
        from auto_learner import auto_verify_jingcai
        auto_verify_jingcai()

    scheduler.add_job(
        auto_verify_wrapper,
        trigger=IntervalTrigger(hours=6),
        id="auto_verify_jingcai",
        name="Jingcai Auto-Verify (6h)",
        replace_existing=True,
    )

    # 增量 NN 重训练（有新结果时触发）：每 6 小时
    def auto_learn_wrapper():
        from auto_learner import auto_learn_trigger
        result = auto_learn_trigger()
        if result.get("triggered"):
            logger.info(
                f"[auto-learn] Triggered: {result['new_results']} new results, "
                f"trained: {result['trained']}"
            )

    scheduler.add_job(
        auto_learn_wrapper,
        trigger=IntervalTrigger(hours=6),
        id="auto_learn_nn",
        name="Auto NN Incremental Training (6h)",
        replace_existing=True,
    )

    # 已结束比赛预测重算（用新引擎含平局检测）：每周一 07:00
    def relock_finished_job():
        """对已结束且有结果的比赛重新运行预测引擎，更新概率（含DrawDetection）
        
        注意：已存在 lock 记录的预测不会覆写，保留赛前原始值。
        """
        from core.prediction_engine import PredictionEngine, build_context_from_match
        with DBSession() as db:
            matches = db.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.closing_odds_home != None,
                Match.closing_odds_home > 1.01,
            ).limit(500).all()

            updated = 0
            skipped = 0
            for match in matches:
                try:
                    existing_locked = db.query(Prediction).filter(
                        Prediction.match_id == match.id,
                        Prediction.locked_at.isnot(None),
                    ).first()
                    if existing_locked:
                        skipped += 1
                        continue

                    ctx = build_context_from_match(match)
                    engine = PredictionEngine(db_session=db)
                    result = engine.predict(ctx)
                    for payload in result.to_db_payload():
                        pred = db.query(Prediction).filter(
                            Prediction.match_id == match.id,
                            Prediction.play_type == payload["play_type"],
                        ).first()
                        if pred:
                            pred.probabilities = payload["probabilities"]
                            pred.confidence = payload.get("confidence")
                            pred.model_version = payload.get("model_version", "v1.0")
                        else:
                            pred = Prediction(
                                match_id=match.id,
                                play_type=payload["play_type"],
                                probabilities=payload["probabilities"],
                                confidence=payload.get("confidence"),
                                model_version=payload.get("model_version", "v1.0"),
                            )
                            db.add(pred)
                    updated += 1
                except Exception as e:
                    logger.debug(f"[relock] Skip {match.match_code}: {e}")

            db.commit()
            logger.info(f"[relock] Updated {updated}, skipped {skipped} (already locked) / {len(matches)} finished matches")

    scheduler.add_job(
        relock_finished_job,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="relock_finished",
        name="Re-lock Finished Match Predictions (with DrawDetection)",
        replace_existing=True,
    )

    # 参数自动寻优：每两周 周一 07:30
    def param_optimize_wrapper():
        from param_optimizer import param_optimize_job
        param_optimize_job()

    scheduler.add_job(
        param_optimize_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=30, week="1-51/2"),
        id="param_optimize",
        name="Strategy Parameter Auto-Optimization (biweekly)",
        replace_existing=True,
    )

    # ── 策略漂移监控：每天 22:00 ──
    def strategy_monitor_wrapper():
        from strategy_monitor import strategy_monitor_job
        strategy_monitor_job()

    scheduler.add_job(
        strategy_monitor_wrapper,
        trigger=CronTrigger(hour=22, minute=0),
        id="strategy_monitor",
        name="Strategy Drift Monitor (daily)",
        replace_existing=True,
    )

    # ── NN 重训练回调: 在 bet_nn 训练之后 ──
    def nn_retrain_monitor_wrapper():
        from strategy_monitor import nn_retrain_callback
        result = nn_retrain_callback()
        logger.info(f"[nn-retrain-monitor] {result['action']}: {result['next_step']}")

    scheduler.add_job(
        nn_retrain_monitor_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=15),
        id="nn_retrain_monitor",
        name="NN Retrain Strategy Callback (weekly)",
        replace_existing=True,
    )

    # 伤停数据同步：每天 08:00
    def injury_sync_wrapper():
        from injury_sync import InjurySync
        with DBSession() as db:
            sync = InjurySync(db)
            updated = sync.sync_upcoming(days=7)
            logger.info(f"[injury-sync] Updated {updated} teams")
            cleared = sync.clear_stale_injuries(days=3)
            logger.info(f"[injury-sync] Cleared {cleared} stale entries")

    scheduler.add_job(
        injury_sync_wrapper,
        trigger=CronTrigger(hour=8, minute=0),
        id="injury_sync",
        name="Injury/Suspension Data Sync (daily)",
        replace_existing=True,
    )



    # ────────────────────────────
    # Task: 实时赛果检测 — 自动更新在售期号的预测结果并关期（每2分钟）
    # ────────────────────────────
    def jingcai_realtime_results_wrapper():
        """
        高频检测：发现已完赛比赛 → 更新预测正确性 → 全完赛则自动开奖。
        解决"在售期号有结果但报告不更新"的问题。
        """
        with DBSession() as db:
            try:
                # 1. 同步外部赛果（只同步最近2天，轻量快速）
                from result_sync import sync_results
                synced = sync_results(db, days_back=2)
                if synced:
                    logger.info(f"[jingcai-realtime] 同步 {synced} 场赛果")

                # 2. 检查在售期号中所有比赛已完赛的，自动关期
                from api.routers.jingcai import _auto_close_expired_issues
                closed = _auto_close_expired_issues(db)
                if closed:
                    logger.info(f"[jingcai-realtime] 自动关期 {closed} 个")

                # 3. 检查刚关期的，立即验证
                from jingcai_predictor import verify_issue
                from database.models import JingcaiIssue
                drawn = db.query(JingcaiIssue).filter(
                    JingcaiIssue.status == "drawn",
                    JingcaiIssue.verification == None,
                ).all()
                for issue in drawn:
                    if issue.draw_result:
                        try:
                            verify_issue(db, issue.issue_id)
                            logger.info(f"[jingcai-realtime] 已验证 {issue.issue_id}")
                        except Exception as e:
                            logger.warning(f"[jingcai-realtime] 验证失败 {issue.issue_id}: {e}")

                # 4. 推送SSE通知 — 通知前端刷新
                if synced or closed:
                    try:
                        from sse import push_event
                        push_event("jingcai_update", {
                            "synced": synced,
                            "closed": closed,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as e:
                        logger.debug(f"[jingcai-realtime] SSE推送失败: {e}")
            except Exception as e:
                logger.error(f"[jingcai-realtime] 执行异常: {e}", exc_info=True)

    scheduler.add_job(
        jingcai_realtime_results_wrapper,
        trigger=IntervalTrigger(minutes=2),
        id="jingcai_realtime_results",
        name="Jingcai Realtime Results (2min)",
        replace_existing=True,
    )

    # ────────────────────────────
    # Task: 足彩期号自动验证 — 检查已开奖但未验证的期号并执行verify
    # ────────────────────────────
    def jingcai_verify_wrapper():
        from scheduler import jingcai_auto_verify_wrapper
        jingcai_auto_verify_wrapper()

    scheduler.add_job(
        jingcai_verify_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="jingcai_auto_verify",
        name="Jingcai Auto-Verify (1h)",
        replace_existing=True,
    )
    # ── Zgzcw 竞彩比赛同步：每 30 分钟 ──
    def zgzcw_jc_sync_wrapper():
        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches() # 移除 db_path 硬编码，使用默认配置
        if result.get("created") or result.get("updated"):
            logger.info(
                f"[zgzcw-jc-sync] Synced {result.get('matches')} matches: "
                f"created={result.get('created')}, updated={result.get('updated')}, "
                f"errors={result.get('errors')}"
            )

    scheduler.add_job(
        zgzcw_jc_sync_wrapper,
        trigger=IntervalTrigger(minutes=30),
        id="zgzcw_jc_sync",
        name="Zgzcw JC Match Sync (live.zgzcw.com)",
        replace_existing=True,
    )

    # ── Fusion 逻辑回归训练：每周一 06:05（含 A/B 验证部署）──
    def data_quality_wrapper():
        from database.models import get_db
        from data_cleaner import DataCleaner
        db = next(get_db())
        try:
            cleaner = DataCleaner(db)
            findings = cleaner.audit()
            critical = [f for f in findings if f.severity == "critical"]
            if critical:
                logger.warning(f"[data-quality] {len(critical)} critical issues found")
                for f in critical:
                    logger.warning(f"  - {f.category}: {f.description}")
                # 自动修复安全项 (时区、0赔率、source)
                result = cleaner.clean(dry_run=False)
                fixed = {k: v for k, v in result.fixed.items() if v > 0}
                if fixed:
                    logger.info(f"[data-quality] Auto-fixed: {fixed}")
            else:
                logger.info(f"[data-quality] {len(findings)} issues found, none critical")
        except Exception as e:
            logger.error(f"[data-quality] Error: {e}")
        finally:
            db.close()

    scheduler.add_job(
        data_quality_wrapper,
        trigger=CronTrigger(hour=5, minute=45),
        id="data_quality_check",
        name="Daily Data Quality Check + Auto-Fix",
        replace_existing=True,
    )

    # ── Fusion 逻辑回归训练：每周一 06:05（含 A/B 验证部署）──
    def fusion_train_wrapper():

        from fusion.validate_deploy import train_with_validation

        result = train_with_validation(
            l1_penalty=0.001,
            class_weight={0: 0.8, 1: 1.5, 2: 0.8},
            val_ratio=0.1,
        )
        if result.get("deployed"):
            logger.info(f"[fusion-train] Deployed new weights (delta_brier={result.get('delta_brier', 'N/A')})")
        elif result.get("decision") == "keep_old":
            logger.warning(f"[fusion-train] New weights rejected (delta_brier={result.get('delta_brier', 'N/A')})")
        else:
            logger.warning(f"[fusion-train] Deployment skipped: {result.get('decision', 'unknown')}")

    scheduler.add_job(
        fusion_train_wrapper,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=5),
        id="fusion_train_weekly",
        name="Fusion LR Training (Global + Leagues + Knockout)",
        replace_existing=True,
    )

    # ── 预测快照生成：每 30 分钟检查一次未来 2 小时比赛 ──
    def prediction_snapshot_wrapper():
        from database.models import SessionLocal
        from core.prediction_snapshot import PredictionSnapshotManager
        with SessionLocal() as db:
            mgr = PredictionSnapshotManager(db)
            count = mgr.generate_for_upcoming(hours=2)
            if count > 0:
                logger.info(f"[snapshot-job] Generated {count} snapshots")

    scheduler.add_job(
        prediction_snapshot_wrapper,
        trigger=CronTrigger(minute="*/30"),
        id="prediction_snapshot_job",
        name="Prediction Snapshot Generation (Upcoming 2h)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[scheduler] All jobs started")


def stop_scheduler():
    """停止所有定时任务"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("[scheduler] All jobs stopped")
    else:
        logger.info("[scheduler] Not running, skip shutdown")


# 获取任务状态
@scheduler.scheduled_job('interval', id='heartbeat', minutes=5)
def heartbeat():
    """心跳检测，记录调度器存活状态"""
    logger.info("[scheduler] Heartbeat OK")
