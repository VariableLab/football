"""验证已生成预测的准确率（按玩法类型拆分）。

解析 JSON probabilities 字段，找到最高概率选项，与实际结果对比。
支持: SPF(胜平负), RQ(让球), Score(比分), Goals(总进球), Half(半全场)。

用法:
  cd backend && python3 validate_predictions.py
  cd backend && python3 validate_predictions.py --with-odds
  cd backend && python3 validate_predictions.py --league EPL
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from sqlalchemy.orm import Session

# Ensure backend/ is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.models import SessionLocal, Match, MatchStatus, Prediction
from core.draw_calibrator import DrawFeatures, apply_draw_calibration, load_draw_params, market_probabilities


# ── 结果映射 ──

def spf_actual(match: Match) -> str | None:
    if match.actual_home_goals is None:
        return None
    h, a = match.actual_home_goals, match.actual_away_goals
    if h > a:
        return "home"
    elif h == a:
        return "draw"
    else:
        return "away"


def rq_actual(match: Match, handicap: int = -1) -> str | None:
    if match.actual_home_goals is None:
        return None
    h, a = match.actual_home_goals, match.actual_away_goals
    adjusted = h + handicap
    if adjusted > a:
        return "home"
    elif adjusted == a:
        return "draw"
    else:
        return "away"


def score_actual(match: Match) -> str | None:
    if match.actual_home_goals is None:
        return None
    h, a = match.actual_home_goals, match.actual_away_goals
    return f"{h}:{a}"


def goals_actual(match: Match) -> str | None:
    if match.actual_home_goals is None:
        return None
    total = match.actual_home_goals + match.actual_away_goals
    if total >= 7:
        return "7+"
    return str(total)


def half_actual(match: Match) -> str | None:
    if match.ht_home_goals is None or match.actual_home_goals is None:
        return None
    ht_h, ht_a = match.ht_home_goals, match.ht_away_goals
    ft_h, ft_a = match.actual_home_goals, match.actual_away_goals

    def outcome(h, a):
        if h > a:
            return "主"
        elif h == a:
            return "平"
        else:
            return "客"

    ht_outcome = outcome(ht_h, ht_a)
    ft_outcome = outcome(ft_h, ft_a)
    return f"{ht_outcome}{ft_outcome}"


ACTUAL_MAP = {
    "spf": spf_actual,
    "rq": rq_actual,
    "score": score_actual,
    "goals": goals_actual,
    "half": half_actual,
}


def parse_probs(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def top_prediction(probs: dict) -> str:
    return max(probs, key=probs.get)


def has_real_odds(match: Match) -> bool:
    for col in ("closing_odds_home", "closing_odds_draw", "closing_odds_away",
                "odds_home", "odds_draw", "odds_away"):
        val = getattr(match, col, None)
        if val and val > 1.01:
            return True
    return False


def _draw_features_for_match(match: Match) -> DrawFeatures:
    market = market_probabilities(
        match.closing_odds_home or match.odds_home,
        match.closing_odds_draw or match.odds_draw,
        match.closing_odds_away or match.odds_away,
    )
    home = match.home_team
    away = match.away_team
    home_xg = (home.avg_xg if home else None) or (home.avg_goals_scored if home else None) or 1.3
    away_xg = (away.avg_xg if away else None) or (away.avg_goals_scored if away else None) or 1.3
    return DrawFeatures(
        elo_diff=((home.elo if home else None) or 1500) - ((away.elo if away else None) or 1500),
        xg_diff=home_xg - away_xg,
        market_draw_prob=market["draw"] if market else None,
        is_knockout=match.stage not in (None, "", "group"),
    )


def validate(db: Session, play_type: str, league: str | None = None,
             with_odds_only: bool = False, draw_calibrated: bool = False) -> dict:
    """Validate predictions for a single play type. Returns stats dict."""
    VALID_OUTCOMES = ("home", "draw", "away")
    matches_q = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.in_(VALID_OUTCOMES),
    )
    if league:
        matches_q = matches_q.filter(Match.competition.like(f"{league}%"))

    matches = matches_q.all()
    match_by_id = {m.id: m for m in matches}

    if not match_by_id:
        return {"total": 0, "correct": 0, "accuracy": 0.0}

    match_ids = list(match_by_id.keys())
    # Handle case-insensitive play_type
    preds = (db.query(Prediction)
             .filter(Prediction.play_type.in_([play_type.lower(), play_type.upper()]),
                     Prediction.match_id.in_(match_ids))
             .all())

    pred_by_match = {p.match_id: p for p in preds}

    total = correct = 0
    total_odds = correct_odds = 0
    by_league = defaultdict(lambda: {"total": 0, "correct": 0})
    brier_sum = 0.0
    brier_count = 0
    draw_params = load_draw_params() if draw_calibrated else None

    for mid, match in match_by_id.items():
        pred = pred_by_match.get(mid)
        if not pred:
            continue

        probs = parse_probs(pred.probabilities)
        if not probs:
            continue
        if draw_calibrated and play_type == "SPF":
            probs = apply_draw_calibration(probs, _draw_features_for_match(match), draw_params)

        actual_fn = ACTUAL_MAP[play_type]
        actual = actual_fn(match)
        if actual is None:
            continue

        predicted = top_prediction(probs)
        is_correct = predicted == actual
        total += 1
        if is_correct:
            correct += 1

        comp = match.competition or "unknown"
        by_league[comp]["total"] += 1
        if is_correct:
            by_league[comp]["correct"] += 1

        # Brier score (for SPF/RQ which have 3 outcomes)
        if play_type in ("spf", "rq"):
            for key in probs:
                outcome = 1.0 if key == actual else 0.0
                brier_sum += (probs[key] - outcome) ** 2
            brier_count += 1

        # With-odds subset
        if with_odds_only and has_real_odds(match):
            total_odds += 1
            if is_correct:
                correct_odds += 1

    accuracy = correct / total if total > 0 else 0.0
    brier = brier_sum / (brier_count * 3) if brier_count > 0 else None

    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "brier": brier,
        "by_league": dict(by_league),
        "total_odds": total_odds,
        "correct_odds": correct_odds,
        "accuracy_odds": correct_odds / total_odds if total_odds > 0 else 0.0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate predictions by play type")
    parser.add_argument("--with-odds", action="store_true", help="Only count matches with real odds")
    parser.add_argument("--league", type=str, default=None, help="Filter by league prefix (e.g. EPL)")
    parser.add_argument("--draw-calibrated", action="store_true", help="Apply draw calibration to SPF validation")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print("=" * 60)
        print(f"  预测验证 {'[有赔率]' if args.with_odds else '[全部]'}"
              f"{' | 平局校准' if args.draw_calibrated else ''}"
              f"{' | 联赛=' + args.league if args.league else ''}")
        print("=" * 60)

        for pt in ["spf", "rq", "score", "goals", "half"]:
            result = validate(
                db,
                pt,
                league=args.league,
                with_odds_only=args.with_odds,
                draw_calibrated=args.draw_calibrated,
            )
            acc = result["accuracy"]
            bri = result["brier"]
            label_map = {"spf": "胜平负", "rq": "让球胜平负", "score": "比分",
                         "goals": "总进球", "half": "半全场"}
            label = label_map.get(pt, pt)

            line = f"  {pt:6s} ({label}): {result['correct']}/{result['total']} = {acc:.1%}"
            if bri is not None:
                line += f"  Brier={bri:.4f}"
            if result["total_odds"] > 0:
                line += f"  | 有赔率: {result['correct_odds']}/{result['total_odds']} = {result['accuracy_odds']:.1%}"
            print(line)

            # Top leagues
            league_stats = result.get("by_league", {})
            if league_stats:
                sorted_leagues = sorted(league_stats.items(),
                                        key=lambda x: -x[1]["total"])[:8]
                for comp, s in sorted_leagues:
                    if s["total"] >= 100:
                        la = s["correct"] / s["total"]
                        print(f"         {comp:16s}: {s['correct']}/{s['total']} = {la:.1%}")

        # Half-time data coverage
        ht_count = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.ht_home_goals.isnot(None)
        ).count()
        total_finished = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED
        ).count()
        print(f"\n  半场数据覆盖: {ht_count}/{total_finished} = {ht_count/total_finished:.1%}")

        print("\n" + "=" * 60)
    finally:
        db.close()
