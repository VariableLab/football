"""
结果同步、竞彩核验、开奖结果相关定时任务
"""
import os
import ssl as _ssl
from datetime import datetime, timedelta, timezone

try:
    import certifi
    _ctx = _ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ctx = _ssl.create_default_context()

from database.models import Match, MatchStatus, JingcaiIssue
from utils.logger import get_logger

logger = get_logger("scheduler.results")

_OPENFOOTBALL_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
_OPENFOOTBALL_SEASON = "2025-26"
_OPENFOOTBALL_LEAGUES = {
    "en.1": "EPL", "de.1": "Bundesliga", "es.1": "LaLiga",
    "it.1": "SerieA", "fr.1": "Ligue1",
    "en.2": "Championship", "de.2": "Bundesliga2",
    "es.2": "LaLiga2", "it.2": "SerieB", "fr.2": "Ligue2",
}
_OPENFOOTBALL_LOCAL = os.getenv("OPENFOOTBALL_LOCAL_DIR", "")


def _fetch_openfootball_json(league_code: str, season: str = _OPENFOOTBALL_SEASON):
    """从 openfootball 获取赛季数据"""
    import urllib.request
    import json as _json
    if _OPENFOOTBALL_LOCAL:
        local_path = os.path.join(_OPENFOOTBALL_LOCAL, season, f"{league_code}.json")
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    return _json.load(f)
            except Exception as e:
                logger.warning(f"[result] Local file error: {e}")
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
    from monitor.scheduler.jobs import DBSession
    from database.models import Match, Team

    try:
        from result_sync import sync_results
        with DBSession() as db:
            count = sync_results(db, days_back=14)
            if count:
                logger.info(f"[result] result_sync 模块同步 {count} 场")
                return
    except Exception as e:
        logger.warning(f"[result] result_sync 模块失败: {e}")

    from openfootball_importer import TeamMatcher

    with DBSession() as db:
        live_matches = db.query(Match).filter(
            Match.status.in_([MatchStatus.LIVE, MatchStatus.UPCOMING]),
            Match.kickoff_at < datetime.now(timezone.utc) - timedelta(minutes=90),
        ).all()

        if not live_matches:
            return

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


def zgzcw_draw_sync_job():
    """从中国足彩网采集开奖结果并更新比赛状态。每 6 小时运行一次。"""
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
    """自动补录已开奖竞彩期号的开奖结果"""
    from sqlalchemy import text
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
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
                "results": results, "prizes": {},
            }, ensure_ascii=False)
            iss.verification = json.dumps({
                "total_matches": total, "known_results": known,
                "home_wins": home_wins, "draws": draws, "away_wins": away_wins,
            }, ensure_ascii=False)
            filled += 1
            logger.info(f"[draw] {iss.issue_id}: {known}/{total} results auto-filled (H={home_wins} D={draws} A={away_wins})")

        if filled:
            db.commit()
            logger.info(f"[draw] {filled} issues auto-filled")


def jingcai_auto_verify_wrapper():
    """检查已开奖但未验证的期号并执行verify"""
    from database.models import JingcaiIssue
    from jingcai_predictor import verify_issue
    from database.config import get_db

    db = next(get_db())
    try:
        try:
            from result_sync import sync_results
            synced = sync_results(db, days_back=14)
            if synced:
                logger.info(f"[jingcai-verify] 预同步 {synced} 场结果")
        except Exception as e:
            logger.warning(f"[jingcai-verify] 预同步失败: {e}")

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


def jingcai_realtime_results_wrapper():
    """高频检测：发现已完赛比赛 → 更新预测正确性 → 全完赛则自动开奖。每2分钟"""
    from monitor.scheduler.jobs import DBSession

    with DBSession() as db:
        try:
            from result_sync import sync_results
            synced = sync_results(db, days_back=2)
            if synced:
                logger.info(f"[jingcai-realtime] 同步 {synced} 场赛果")

            from api.routers.jingcai import _auto_close_expired_issues
            closed = _auto_close_expired_issues(db)
            if closed:
                logger.info(f"[jingcai-realtime] 自动关期 {closed} 个")

            from database.models import JingcaiIssue
            from jingcai_predictor import verify_issue
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

            if synced or closed:
                try:
                    from sse import push_event
                    push_event("jingcai_update", {
                        "synced": synced, "closed": closed,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as e:
                    logger.debug(f"[jingcai-realtime] SSE推送失败: {e}")
        except Exception as e:
            logger.error(f"[jingcai-realtime] 执行异常: {e}", exc_info=True)
