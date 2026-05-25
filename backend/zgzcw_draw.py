"""
zgzcw_draw.py — 从中国足彩网（zgzcw.com）获取竞彩足球开奖结果

数据源: https://cp.zgzcw.com/dc/getKaijiangFootBall.action
返回 HTML 表格（服务端渲染，无需 JS），覆盖全部联赛的竞彩开奖结果。

流程:
  1. 按日期查询 zgzcw 开奖结果
  2. 解析 HTML 表格 → 结构化数据 SPF 编码
  3. 队名归一化后匹配本地 JC 比赛
  4. 更新 Match.actual_outcome + status = FINISHED
  5. 依赖 fill_drawn_issues_job 补 JingcaiIssue.draw_result
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from models import Match, MatchStatus, Team
from logger import get_logger
from zgzcw_source import TEAM_NAME_ALIAS, _normalise_team_name

logger = get_logger("zgzcw_draw")

# ────────────────────────────
# 中国足彩网队名 → 数据库队名 补充映射
# zgzcw 有时用简称，数据库用全称/别称
# ────────────────────────────
ZGZCW_TO_DB: Dict[str, str] = {
    "迈阿密": "迈国际",
    "巴黎圣曼": "巴黎圣日耳曼",
    "斯特拉斯": "斯特拉斯堡",
    "圣埃蒂安": "圣埃蒂安",
    "皇馬": "皇家马德里",
    "奥维耶多": "奥维耶多",
    "罗达JC": "罗达JC",
    "埃蒙": "埃蒙",
    "赫拉克莱斯": "赫拉克莱斯",
    "FC埃因霍温": "FC埃因霍温",
    "奥斯": "奥斯",
    "阿尔青年": "阿尔青年",
    "精英": "精英",
    "勒沃库森": "勒沃库森",
    "布鲁马波": "布鲁马波",
    "埃夫斯堡": "埃夫斯堡",
    "埃尔夫斯堡": "埃尔夫斯堡",
    "哈姆斯塔德": "哈姆斯塔德",
    "布兰": "布兰",
    "KFUM奥斯陆": "KFUM奥斯陆",
    "KF奥斯陆": "KFUM奥斯陆",
    "盖斯": "盖斯",
    "代格福什": "代格福什",
    "哈尔姆斯": "哈尔姆斯塔德",
    "天狼星": "天狼星",
    "索尔纳": "索尔纳",
    "赫根": "赫根本",
    "FC首尔": "首尔FC",
    "仁川联队": "仁川联",
    "迈阿密": "迈国际",
    "达马克": "达马克",
    "布赖代": "布赖代合作",
    "吉达国民": "吉达国民",
    "利雅青年": "利雅青年",
    "布雷西亚": "布雷西亚",
    "凯泽斯劳滕": "凯泽",
    "圣图尔登": "圣图尔登",
    "前进之鹰": "前进之鹰",
    "兹沃勒": "兹沃勒",
    "多德勒支": "多德勒支",
    "海牙": "海牙",
    "芬洛": "芬洛",
    "马斯特里": "马斯特里",
    "坎布尔": "坎布尔",
    "乌德青年": "乌德勒支青年",
    "阿贾青年": "阿贾克斯青年",
    "登博斯": "登博思",
    "格拉夫": "格拉夫夏普",
    "维迪斯": "维迪斯",
    "FC埃因霍温": "FC埃因霍温",
    "洛默尔": "洛默尔",
    "贝弗伦": "贝弗伦",
    "通德拉": "通德拉",
    "甘马雷斯": "甘马雷斯",
    "卡萨匹亚": "卡萨匹亚",
    "国民队": "国民队",
    "费雷拉": "费雷拉",
    "莱里亚": "莱里亚",
    "维泽拉": "维泽拉",
    "查维斯": "查维斯",
    "摩雷伦斯": "摩雷伦斯",
    "AVS": "AVS",
    "里奥阿维": "里奥阿维",
    "埃斯托里": "埃斯托里尔",
    "博阿维斯塔": "博阿维斯塔",
    "法伦斯": "法伦斯",
    "圣克拉拉": "圣克拉拉",
    "吉维森特": "吉维森特",
    "布兰": "布兰",
}

_ZG_CACHE: Dict[str, List[Dict]] = {}


def _safe_float(val: Any) -> Optional[float]:
    try:
        v = float(str(val).strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ────────────────────────────
# 1. 获取开奖结果页面
# ────────────────────────────

def fetch_page(date_from: str, date_to: str) -> Optional[str]:
    """从 zgzcw.com 获取指定日期范围的开奖结果 HTML"""
    url = "https://cp.zgzcw.com/dc/getKaijiangFootBall.action"
    params = {"startTime": date_from, "endTime": date_to}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = httpx.get(url, params=params, headers=headers, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"[zgzcw-draw] fetch {date_from}~{date_to} failed: {e}")
        return None


# ────────────────────────────
# 2. 解析 HTML → 结构化数据
# ────────────────────────────

def parse_html(html: str) -> List[Dict]:
    """
    解析 zgzcw 开奖结果 HTML 表格。

    返回: [{date, home, away, home_goals, away_goals, outcome, ...}]
    outcome: 'home' | 'draw' | 'away' (SPF 彩果)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tr")
    results: List[Dict] = []

    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 8:
            continue

        cols = [td.get_text(strip=True) for td in tds]
        code = cols[0]

        if not code or not re.match(r"周[一二三四五六日]", code):
            continue

        home_name = cols[3]
        away_name = cols[5]
        if not home_name or not away_name:
            continue

        spf_outcome_raw = cols[6].strip()
        score_raw = cols[4].strip()
        score_raw = score_raw.replace("\r", "").replace("\n", "").replace(" ", "")

        home_goals, away_goals = None, None
        if ":" in score_raw:
            parts = score_raw.split(":")  # "3:2(1:1)" -> ["3", "2(1", "1)"]
            if len(parts) >= 2:
                try:
                    fh = parts[0].strip()
                    sa = parts[1].strip()
                    # 提取客队进球（可能在括号前）
                    away_raw = sa.split("(")[0].strip() if "(" in sa else sa
                    home_goals = int(fh)
                    away_goals = int(away_raw)
                except (ValueError, IndexError):
                    pass

        outcome = None
        if spf_outcome_raw == "胜":
            outcome = "home"
        elif spf_outcome_raw == "负":
            outcome = "away"
        elif spf_outcome_raw == "平":
            outcome = "draw"

        if outcome is None and home_goals is not None and away_goals is not None:
            if home_goals > away_goals:
                outcome = "home"
            elif home_goals < away_goals:
                outcome = "away"
            else:
                outcome = "draw"

        results.append({
            "code": code,
            "home": home_name,
            "away": away_name,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "spf_outcome": spf_outcome_raw,
            "outcome": outcome,
        })

    return results


# ────────────────────────────
# 3. 队名匹配
# ────────────────────────────

def _normalise_both(name: str) -> str:
    """依次用 TEAM_NAME_ALIAS 和 ZGZCW_TO_DB 归一化队名"""
    name = name.strip()
    name = TEAM_NAME_ALIAS.get(name, name)
    name = ZGZCW_TO_DB.get(name, name)
    return name


def match_local_team(name: str, team_name_index: Dict[str, int]) -> Optional[int]:
    """将归一化后的队名匹配到本地 team_id"""
    norm = _normalise_both(name)
    if norm in team_name_index:
        return team_name_index[norm]

    norm_lower = norm.lower()
    for db_name, tid in team_name_index.items():
        if norm_lower == db_name.lower():
            return tid
        if (len(norm) >= 3 and len(db_name) >= 3 and
                (norm in db_name or db_name in norm)):
            return tid

    return None


def match_and_update(results: List[Dict], db, date_str: str, team_index: Dict[str, int]) -> Tuple[int, int]:
    """
    将zgzcw开奖结果匹配到本地JC比赛并更新。

    返回: (matched, updated)
    """
    matched = 0
    updated = 0

    pending = (
        db.query(Match)
        .filter(
            Match.match_code.like("JC-%"),
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
            Match.kickoff_at.isnot(None),
        )
        .all()
    )

    for r in results:
        home_norm = _normalise_both(r["home"])
        away_norm = _normalise_both(r["away"])

        best_match = None
        best_score = 0

        date_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        yesterday = (date_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        for m in pending:
            if m.kickoff_at is None:
                continue
            m_date = m.kickoff_at
            if hasattr(m_date, "strftime"):
                m_date_str = m_date.strftime("%Y-%m-%d")
            else:
                m_date_str = str(m_date)[:10]

            if m_date_str not in (date_str, yesterday, tomorrow):
                continue

            ht = m.home_team
            at = m.away_team
            if not ht or not at:
                continue

            ht_norm = _normalise_both(ht.name or "")
            at_norm = _normalise_both(at.name or "")

            score = 0
            if home_norm == ht_norm:
                score += 2
            elif home_norm in ht_norm or ht_norm in home_norm:
                score += 1

            if away_norm == at_norm:
                score += 2
            elif away_norm in at_norm or at_norm in away_norm:
                score += 1

            if score == 4:  # 完美匹配
                best_match = m
                best_score = score
                break

            if score > best_score and score >= 2:
                best_match = m
                best_score = score

        if best_match and best_score >= 3:
            matched += 1
            if r["outcome"] and best_match.status != MatchStatus.FINISHED:
                best_match.actual_home_goals = r["home_goals"]
                best_match.actual_away_goals = r["away_goals"]
                best_match.actual_outcome = r["outcome"]
                best_match.status = MatchStatus.FINISHED
                best_match.updated_at = datetime.now(timezone.utc)
                updated += 1
                logger.info(
                    f"[zgzcw-draw] ✓ {best_match.match_code}: "
                    f"{ht_norm} {r['home_goals']}-{r['away_goals']} {at_norm} "
                    f"(outcome={r['outcome']})"
                )

    if updated:
        db.commit()

    return matched, updated


# ────────────────────────────
# 4. 调度器入口
# ────────────────────────────

def sync_draw_results_for_date(date_str: str, db) -> Tuple[int, int]:
    """
    同步指定日期的开奖结果。

    返回: (matched, updated)
    """
    cache_key = date_str
    if cache_key not in _ZG_CACHE:
        html = fetch_page(date_str, date_str)
        if not html:
            return 0, 0
        _ZG_CACHE[cache_key] = parse_html(html)

    results = _ZG_CACHE[cache_key]
    if not results:
        return 0, 0

    team_index = _build_team_name_index(db)
    return match_and_update(results, db, date_str, team_index)


def sync_recent_draw(days_back: int = 14) -> Dict:
    """
    同步近期所有待更新 JC 比赛的开奖结果。

    逻辑：
      1. 遍历过去 days_back 天中仍有未结束 JC 比赛的日期
      2. 从 zgzcw 获取该日期的开奖结果
      3. 匹配并更新数据库

    被 scheduler 定时调用。
    """
    from models import SessionLocal

    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc)
        total_matched = 0
        total_updated = 0
        errors = []

        dates_to_check = set()
        matching_pending = (
            db.query(Match)
            .filter(
                Match.match_code.like("JC-%"),
                Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
                Match.kickoff_at.isnot(None),
            )
            .all()
        )
        for m in matching_pending:
            if m.kickoff_at:
                d = m.kickoff_at
                if hasattr(d, "strftime"):
                    ds = d.strftime("%Y-%m-%d")
                else:
                    ds = str(d)[:10]
                if ds < (today - timedelta(days=days_back)).strftime("%Y-%m-%d"):
                    continue
                if ds <= today.strftime("%Y-%m-%d"):
                    dates_to_check.add(ds)

        sorted_dates = sorted(dates_to_check)
        if sorted_dates:
            logger.info(
                f"[zgzcw-draw] checking {len(sorted_dates)} dates: "
                f"{sorted_dates[0]} ~ {sorted_dates[-1]}"
            )

        team_index = _build_team_name_index(db)

        for ds in sorted_dates:
            cache_key = ds
            if cache_key not in _ZG_CACHE:
                html = fetch_page(ds, ds)
                if not html:
                    errors.append(f"fetch failed: {ds}")
                    continue
                _ZG_CACHE[cache_key] = parse_html(html)

            results = _ZG_CACHE[cache_key]
            matched, updated = match_and_update(results, db, ds, team_index)
            total_matched += matched
            total_updated += updated

        if total_updated:
            logger.info(
                f"[zgzcw-draw] sync done: matched={total_matched} "
                f"updated={total_updated} errors={len(errors)}"
            )
        else:
            logger.debug(
                f"[zgzcw-draw] sync done: matched={total_matched} "
                f"updated={total_updated}"
            )

        if total_updated > 0:
            autodrawn = _auto_draw_ready_issues(db)
            filled = _fill_issue_draw_results(db)

        return {
            "dates_checked": len(sorted_dates),
            "matched": total_matched,
            "updated": total_updated,
            "autodrawn": autodrawn if total_updated > 0 else 0,
            "filled": filled if total_updated > 0 else 0,
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"[zgzcw-draw] sync error: {e}")
        return {"error": str(e)}
    finally:
        db.close()


def _auto_draw_ready_issues(db) -> int:
    """自动将全部比赛已完成的 on_sale 期号 → drawn

    扫描 status='on_sale' 的期号，若所有关联比赛都有 actual_outcome，
    则自动设置 status='drawn' 以便 _fill_issue_draw_results 填充开奖结果。
    """
    from models import JingcaiIssue, Match, MatchStatus
    from sqlalchemy import text

    autodrawn = 0
    ready_issues = (
        db.query(JingcaiIssue)
        .filter(
            JingcaiIssue.status == "on_sale",
            JingcaiIssue.draw_at.is_(None),
        )
        .all()
    )

    for iss in ready_issues:
        rows = db.execute(
            text(
                """
            SELECT m.id, m.actual_outcome
            FROM jingcai_issue_matches jim
            JOIN matches m ON m.id = jim.match_id
            WHERE jim.issue_id = :iid
            ORDER BY jim.sequence
            """
            ),
            {"iid": iss.id},
        ).fetchall()

        if not rows:
            continue
        all_known = all(r[1] is not None for r in rows)
        if not all_known:
            continue

        iss.status = "drawn"
        autodrawn += 1

        match_ids = [r[0] for r in rows]
        db.execute(
            text(
                "UPDATE matches SET status = :st WHERE id = ANY(:ids)"
                " AND status != :st"
            ),
            {"st": MatchStatus.FINISHED.value, "ids": match_ids},
        )
        logger.info(
            f"[zgzcw-draw] auto-draw issue {iss.issue_id}: "
            f"{len(rows)} matches all known"
        )

    if autodrawn:
        db.commit()
    return autodrawn


def _fill_issue_draw_results(db) -> int:
    """填充已完成比赛的期号 draw_result（替代 scheduler.fill_drawn_issues_job 避免循环导入）"""
    from models import JingcaiIssue
    from sqlalchemy import text

    filled = 0
    drawn_issues = (
        db.query(JingcaiIssue)
        .filter(
            JingcaiIssue.status == "drawn",
            JingcaiIssue.draw_result.is_(None),
        )
        .all()
    )

    for iss in drawn_issues:
        rows = db.execute(
            text(
                """
            SELECT m.match_code, m.actual_outcome
            FROM jingcai_issue_matches jim
            JOIN matches m ON m.id = jim.match_id
            WHERE jim.issue_id = :iid
            ORDER BY jim.sequence
            """
            ),
            {"iid": iss.id},
        ).fetchall()

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

        iss.draw_result = json.dumps(
            {"results": results, "prizes": {}}, ensure_ascii=False
        )
        iss.verification = json.dumps(
            {
                "total_matches": total,
                "known_results": known,
                "home_wins": home_wins,
                "draws": draws,
                "away_wins": away_wins,
            },
            ensure_ascii=False,
        )
        filled += 1
        logger.info(
            f"[zgzcw-draw] issue {iss.issue_id}: {known}/{total} results "
            f"filled (H={home_wins} D={draws} A={away_wins})"
        )

    if filled:
        db.commit()
    return filled


def _build_team_name_index(db) -> Dict[str, int]:
    teams = db.query(Team).all()
    index: Dict[str, int] = {}
    for t in teams:
        if t.name:
            norm = _normalise_both(t.name)
            if norm not in index:
                index[norm] = t.id
    return index
