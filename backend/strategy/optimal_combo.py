"""
智能串关推荐引擎 - 优化版（带缓存）
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session, joinedload
from database.models import JingcaiIssue, JingcaiIssueMatch, Match, Prediction
import time

# 全局缓存：{issue_id: {"data": result, "expires": timestamp}}
_combo_cache = {}
CACHE_TTL = 300  # 5 分钟缓存

def calc_ev(prob: float, odds: float) -> float:
    if prob <= 0 or odds <= 0: return -1.0
    return (prob * odds) - 1.0

def get_selection_label(play_type: str, selection: str, handicap: Optional[int] = None) -> str:
    if play_type == 'SPF': return {'home': '主胜', 'draw': '平', 'away': '客胜'}.get(selection, selection)
    elif play_type == 'RQ':
        base = {'home': '让胜', 'draw': '让平', 'away': '让负'}.get(selection, selection)
        return f"{base}({handicap:+d})" if handicap else base
    elif play_type == 'GOALS': return f"{selection}球"
    elif play_type == 'HALF': return {'主主':'主主','主平':'主平','主客':'主客','平主':'平主','平平':'平平','平客':'平客','客主':'客主','客平':'客平','客客':'客客'}.get(selection, selection)
    elif play_type == 'SCORE': return selection
    return selection

def generate_rationale(play_type: str, selection: str, prob: float, odds: float, ev: float, home: str, away: str, home_elo: Optional[int], away_elo: Optional[int]) -> str:
    r = []
    if prob >= 0.6: r.append(f"概率{prob*100:.0f}%高")
    elif prob >= 0.5: r.append(f"概率{prob*100:.0f}%稳")
    if ev >= 0.15: r.append(f"EV+{ev*100:.0f}%优")
    elif ev >= 0.05: r.append(f"EV+{ev*100:.0f}%")
    if odds >= 2.5: r.append(f"赔率{odds:.2f}")
    if home_elo and away_elo:
        d = home_elo - away_elo
        if d > 100 and selection == 'home': r.append(f"{home}强")
        elif d < -100 and selection == 'away': r.append(f"{away}强")
    return " | ".join(r) if r else "数据优选"

def compute_optimal_combo(db: Session, issue_id: int, top_n: int = 8, min_prob: float = 0.25, min_ev: float = -0.15, max_per_match: int = 2) -> List[Dict[str, Any]]:
    # 检查缓存
    current_time = time.time()
    if issue_id in _combo_cache and _combo_cache[issue_id]["expires"] > current_time:
        return _combo_cache[issue_id]["data"]
    
    # 获取期号
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.id == issue_id).first()
    if not issue: return []
    
    # 获取所有比赛（优化查询）
    ims = db.query(JingcaiIssueMatch).options(
        joinedload(JingcaiIssueMatch.match).joinedload(Match.home_team),
        joinedload(JingcaiIssueMatch.match).joinedload(Match.away_team),
        joinedload(JingcaiIssueMatch.match).joinedload(Match.predictions)
    ).filter(JingcaiIssueMatch.issue_id == issue_id).all()
    
    if not ims: return []
    
    all_picks = []
    for im in ims:
        m = im.match
        if not m or not m.predictions: continue
        
        home = m.home_team.name if m.home_team else "主队"
        away = m.away_team.name if m.away_team else "客队"
        home_elo = m.home_team.elo if m.home_team and m.home_team.elo else None
        away_elo = m.away_team.elo if m.away_team and m.away_team.elo else None
        odds_h, odds_d, odds_a = m.odds_home or 2.0, m.odds_draw or 3.2, m.odds_away or 3.5
        
        for pred in m.predictions:
            pt = pred.play_type.value if hasattr(pred.play_type, 'value') else str(pred.play_type)
            for sel, p in (pred.probabilities or {}).items():
                if p < min_prob: continue
                odds = odds_h if sel == 'home' else (odds_a if sel == 'away' else odds_d)
                ev = calc_ev(p, odds)
                if ev < min_ev: continue
                all_picks.append({
                    "match_id": m.id, "match_code": m.match_code or "", "home": home, "away": away,
                    "kickoff_at": m.kickoff_at, "play_type": pt, "selection": sel,
                    "selection_label": get_selection_label(pt, sel, im.handicap),
                    "probability": p, "odds": odds, "ev": ev, "handicap": im.handicap,
                    "rationale": generate_rationale(pt, sel, p, odds, ev, home, away, home_elo, away_elo)
                })
    
    # 排序并去重
    all_picks.sort(key=lambda x: x["ev"], reverse=True)
    result, seen = [], set()
    for p in all_picks:
        if len(result) >= top_n: break
        if p["match_id"] not in seen:
            result.append({**p, "probability": round(p["probability"],4), "odds": round(p["odds"],2), "ev": round(p["ev"],4), "kickoff_at": p["kickoff_at"].isoformat() if p["kickoff_at"] else None})
            seen.add(p["match_id"])
    
    # 写入缓存
    _combo_cache[issue_id] = {"data": result, "expires": current_time + CACHE_TTL}
    return result
