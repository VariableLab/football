from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List, Dict, Any
import json
from datetime import datetime, timedelta

from database.models import get_db, JingcaiIssue, JingcaiIssueMatch, Match, MatchStatus
from schemas import (
    JingcaiIssueCreate, JingcaiIssueOut, JingcaiIssueResultIn,
    JingcaiIssueListResponse, JingcaiReportResponse, MatchOut, StatusResponse,
    OptimalComboResponse
)
from utils.logger import get_logger
from api.auth import verify_admin_key

logger = get_logger("jingcai_router")

router = APIRouter(prefix="/api/jingcai", tags=["Jingcai"])

# ────────────────────────────
# Helpers
# ────────────────────────────

def _convert_jingcai_odds(raw: dict, pool: str) -> dict | None:
    """将体彩原始赔率键名转为前端友好的格式"""
    if not raw:
        return None
    result = {}

    def _safe(val) -> float | None:
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    if pool == "rq":
        h = _safe(raw.get("h"))
        d = _safe(raw.get("d"))
        a = _safe(raw.get("a"))
        if h: result["home"] = h
        if d: result["draw"] = d
        if a: result["away"] = a
        if not result:
            return None

    elif pool == "score":
        import re
        for k, v in raw.items():
            m = re.match(r"s(\d+)s(\d+)$", k)
            if m:
                score_key = f"{int(m.group(1))}:{int(m.group(2))}"
                odd = _safe(v)
                if odd:
                    result[score_key] = odd
        other_map = {"s1sh": "主胜其他", "s1sd": "平其他", "s1sa": "客胜其他"}
        for k, label in other_map.items():
            odd = _safe(raw.get(k))
            if odd:
                result[label] = odd
        if not result:
            return None

    elif pool == "goals":
        for i in range(8):
            key = f"s{i}"
            odd = _safe(v) if (v := raw.get(key)) else None
            label = f"{i}" if i < 7 else "7+"
            if odd:
                result[label] = odd
        if not result:
            return None

    elif pool == "half":
        mapping = {
            "hh": "主主", "hd": "主平", "ha": "主客",
            "dh": "平主", "dd": "平平", "da": "平客",
            "ah": "客主", "ad": "客平", "aa": "客客",
        }
        for k, label in mapping.items():
            odd = _safe(raw.get(k))
            if odd:
                result[label] = odd
        if not result:
            return None

    return result


def _safe_parse_json(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    return val


def _issue_to_dict(issue: JingcaiIssue) -> dict:
    matches = []
    for im in (issue.issue_matches or []):
        if im.match is None:
            continue
        match_data = MatchOut.model_validate(im.match).model_dump()
        matches.append({
            "sequence": im.sequence,
            "handicap": im.handicap,
            "rq_odds": _convert_jingcai_odds(json.loads(im.rq_odds), "rq") if im.rq_odds else None,
            "score_odds": _convert_jingcai_odds(json.loads(im.score_odds), "score") if im.score_odds else None,
            "goals_odds": _convert_jingcai_odds(json.loads(im.goals_odds), "goals") if im.goals_odds else None,
            "half_odds": _convert_jingcai_odds(json.loads(im.half_odds), "half") if im.half_odds else None,
            "match": match_data,
        })
    return {
        "id": issue.id,
        "issue_id": issue.issue_id,
        "issue_type": issue.issue_type,
        "status": issue.status,
        "sale_start": issue.sale_start.isoformat() if issue.sale_start else None,
        "sale_end": issue.sale_end.isoformat() if issue.sale_end else None,
        "draw_at": issue.draw_at.isoformat() if issue.draw_at else None,
        "draw_result": _safe_parse_json(issue.draw_result),
        "verification": _safe_parse_json(issue.verification),
        "notes": issue.notes,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "matches": matches,
    }


def _auto_close_expired_issues(db: Session) -> int:
    now = datetime.utcnow()
    expired = []

    sale_end_expired = db.query(JingcaiIssue).filter(
        JingcaiIssue.status == "on_sale",
        JingcaiIssue.sale_end != None,
        JingcaiIssue.sale_end < now,
    ).all()
    expired.extend(sale_end_expired)

    all_on_sale = db.query(JingcaiIssue).filter(
        JingcaiIssue.status == "on_sale",
    ).all()
    for issue in all_on_sale:
        if issue in expired:
            continue
        match_ids = [im.match_id for im in (issue.issue_matches or [])]
        if not match_ids:
            continue
        unfinished = db.query(Match).filter(
            Match.id.in_(match_ids),
            (Match.status != MatchStatus.FINISHED) | (Match.actual_outcome == None),
        ).count()
        if unfinished == 0:
            expired.append(issue)
            continue
        stale = db.query(Match).filter(
            Match.id.in_(match_ids),
            Match.kickoff_at != None,
            Match.kickoff_at < now - timedelta(hours=48),
            Match.actual_outcome == None,
        ).all()
        if stale:
            for m in stale:
                m.status = MatchStatus.FINISHED
                m.actual_outcome = "abandoned"
                m.actual_home_goals = -1
                m.actual_away_goals = -1
                logger.warning(f"[auto-close] 标记比赛 {m.match_code or m.id} 为无结果(超48h)")
            db.commit()
            unfinished = db.query(Match).filter(
                Match.id.in_(match_ids),
                (Match.status != MatchStatus.FINISHED) | (Match.actual_outcome == None),
            ).count()
            if unfinished == 0:
                expired.append(issue)

    for issue in expired:
        issue.status = "drawn"
        issue.draw_result = {"results": [], "auto_closed": True}
    if expired:
        db.commit()
        logger.info(f"[auto-close] 关闭了 {len(expired)} 个期号: {[e.issue_id for e in expired]}")
    return len(expired)

# ────────────────────────────
# Endpoints
# ────────────────────────────

@router.post("/issues/auto-close")
def auto_close_issues(_: bool = Depends(verify_admin_key), db: Session = Depends(get_db)):
    """手动触发过期期号自动关闭。"""
    count = _auto_close_expired_issues(db)
    return {"closed": count}


@router.get("/issues", response_model=JingcaiIssueListResponse)
def list_jingcai_issues(
    status: Optional[str] = None,
    limit: int = 20, offset: int = 0,
    db: Session = Depends(get_db),
):
    """列出足彩期号列表（只读）。"""
    query = db.query(JingcaiIssue).options(
        joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match)
    )
    if status:
        query = query.filter(JingcaiIssue.status == status)
    total = query.count()
    issues = query.offset(offset).limit(min(limit, 100)).all()
    issues.sort(key=lambda i: (0 if i.status == "on_sale" else 1, i.issue_id or ""), reverse=False)
    issues.sort(key=lambda i: (0 if i.status == "on_sale" else 1))
    return {"total": total, "offset": offset, "limit": limit, "items": [_issue_to_dict(i) for i in issues]}


@router.get("/issues/{issue_id}", response_model=JingcaiIssueOut)
def get_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
):
    """获取单期足彩详情（含比赛和预测）"""
    issue = db.query(JingcaiIssue).options(
        joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match).joinedload(Match.predictions)
    ).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_to_dict(issue)


@router.post("/issues", response_model=JingcaiIssueOut)
def create_jingcai_issue(
    data: JingcaiIssueCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """创建足彩期号并关联比赛"""
    from core.jingcai_predictor import create_issue
    try:
        issue = create_issue(
            db,
            issue_id=data.issue_id,
            issue_type=data.issue_type,
            sale_start=data.sale_start,
            sale_end=data.sale_end,
            match_codes=data.match_codes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return issue


@router.post("/issues/{issue_id}/predict")
def predict_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """为整期足彩生成预测"""
    from core.jingcai_predictor import predict_issue
    try:
        result = predict_issue(db, issue_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.post("/issues/{issue_id}/results")
def record_jingcai_result(
    issue_id: str,
    data: JingcaiIssueResultIn,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """录入开奖结果"""
    from core.jingcai_predictor import record_draw_result
    try:
        issue = record_draw_result(
            db, issue_id, data.results, data.prizes, data.draw_at
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return issue


@router.post("/issues/{issue_id}/verify")
def verify_jingcai_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """验证模型预测 vs 开奖结果"""
    from core.jingcai_predictor import verify_issue
    try:
        result = verify_issue(db, issue_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/report", response_model=JingcaiReportResponse)
def jingcai_report(db: Session = Depends(get_db)):
    """每期预测报告：最高胜率模型估算 + 结果自分析"""
    issues = (
        db.query(JingcaiIssue)
        .options(joinedload(JingcaiIssue.issue_matches).joinedload(JingcaiIssueMatch.match).joinedload(Match.predictions))
        .order_by(JingcaiIssue.issue_id.desc())
        .limit(50)
        .all()
    )

    reports = []
    for issue in issues:
        matches_report = []
        for im in (issue.issue_matches or []):
            match = im.match
            if not match:
                continue

            preds = match.predictions or []
            best_pick = None
            best_prob = 0.0

            for p in preds:
                ptype = p.play_type.value if hasattr(p.play_type, "value") else str(p.play_type)
                probs = p.probabilities or {}
                for sel, prob in probs.items():
                    if prob > best_prob:
                        best_prob = prob
                        best_pick = {
                            "play_type": ptype,
                            "selection": sel,
                            "probability": prob,
                        }

            home = match.home_team.name if match.home_team else "?"
            away = match.away_team.name if match.away_team else "?"

            mr = {
                "sequence": im.sequence,
                "home": home,
                "away": away,
                "handicap": im.handicap,
                "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
                "best_pick": best_pick,
                "actual_outcome": match.actual_outcome.value if hasattr(match.actual_outcome, "value") else str(match.actual_outcome) if match.actual_outcome else None,
                "actual_home_goals": match.actual_home_goals,
                "actual_away_goals": match.actual_away_goals,
            }

            spf_pred = None
            for p in preds:
                ptype = p.play_type.value if hasattr(p.play_type, "value") else str(p.play_type)
                if ptype == "SPF":
                    spf_pred = p
                    break

            if match.actual_outcome == "abandoned":
                mr["correct"] = None
                mr["actual_outcome"] = None
            elif match.actual_outcome and spf_pred:
                probs = spf_pred.probabilities or {}
                best_sel = max(probs, key=probs.get) if probs else None
                actual = match.actual_outcome.value if hasattr(match.actual_outcome, "value") else str(match.actual_outcome)
                mr["correct"] = best_sel == actual
                if best_sel:
                    best_pick = {
                        "play_type": "SPF",
                        "selection": best_sel,
                        "probability": probs[best_sel],
                    }
                    mr["best_pick"] = best_pick
            elif match.actual_outcome and best_pick:
                mr["correct"] = None

            matches_report.append(mr)

        raw = issue.verification
        verified = {}
        if isinstance(raw, dict):
            verified = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                verified = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                pass
        total = len(issue.issue_matches or [])
        spf_hits = verified.get("spf_hits", 0)
        if spf_hits == 0 and total > 0:
            spf_hits = sum(1 for m in matches_report if m.get("correct") is True)
        valid_total = sum(1 for m in matches_report if m.get("correct") is not None)
        accuracy = spf_hits / valid_total if valid_total > 0 else 0

        r9_hits = verified.get("r9_hits", 0)
        analysis = _build_issue_analysis(issue, matches_report, spf_hits, total, accuracy)

        reports.append({
            "issue_id": issue.issue_id,
            "issue_type": issue.issue_type,
            "status": issue.status,
            "sale_end": issue.sale_end.isoformat() if issue.sale_end else None,
            "draw_at": issue.draw_at.isoformat() if issue.draw_at else None,
            "total_matches": total,
            "spf_hits": spf_hits,
            "accuracy": accuracy,
            "r9_hits": r9_hits,
            "analysis": analysis,
            "matches": matches_report,
        })

    return {"reports": reports}


def _build_issue_analysis(issue, matches_report, spf_hits, total, accuracy):
    has_results = any(m.get("actual_outcome") for m in matches_report)
    if not has_results:
        return "待开奖"
    if issue.status == "on_sale":
        return "在售中"
    if total == 0:
        return "无比赛数据"

    parts = [f"命中 {spf_hits}/{total} ({accuracy:.1%})"]
    if accuracy >= 0.60:
        parts.append("表现优秀，模型预测准确率高于基准")
    elif accuracy >= 0.45:
        parts.append("表现正常，接近历史平均水准")
    else:
        parts.append("表现偏差，需关注该期赛事特征")

    wrong_matches = [m for m in matches_report if m.get("correct") is False]
    high_conf_miss = [m for m in wrong_matches if m.get("best_pick", {}).get("probability", 0) >= 0.55]
    if high_conf_miss:
        parts.append(f"高置信度未命中 {len(high_conf_miss)} 场，可能存在系统性偏差")

    correct_matches = [m for m in matches_report if m.get("correct") is True]
    low_conf_hit = [m for m in correct_matches if m.get("best_pick", {}).get("probability", 0) < 0.45]
    if low_conf_hit:
        parts.append(f"低置信度命中 {len(low_conf_hit)} 场，含运气成分")

    return "；".join(parts)


# ─── 智能串关推荐 ───────────────────────────────
@router.get("/issues/{issue_id}/optimal-combo", response_model=OptimalComboResponse)
def get_optimal_combo(issue_id: int, top_n: int = 8, db: Session = Depends(get_db)):
    """获取当期最优串关推荐"""
    from optimal_combo import compute_optimal_combo
    picks = compute_optimal_combo(db, issue_id, top_n)
    return {"issue_id": issue_id, "picks": picks, "total": len(picks)}
