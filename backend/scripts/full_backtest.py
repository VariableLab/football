"""
全量历史回测脚本

两种模式:
  1. 验证模式 (默认): 使用数据库中已有的预测结果做回测 (快)
  2. 重算模式 (--fresh): 重新跑预测引擎 (慢, 约30min+)

用法:
  cd backend && python3 scripts/full_backtest.py                    # 验证模式
  cd backend && python3 scripts/full_backtest.py --fresh            # 重算模式
  cd backend && python3 scripts/full_backtest.py --play-types SPF   # 只验证SPF
  cd backend && python3 scripts/full_backtest.py --output report.json
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


from database.models import (
    SessionLocal, Match, MatchStatus, Prediction, PlayType, Team
)
from core.prediction_engine import PredictionEngine, build_context_from_match
from utils.logger import get_logger

logger = get_logger("full_backtest")

PLAY_TYPE_LABELS = {
    "SPF": "胜平负",
    "RQ": "让球胜平负",
    "SCORE": "比分",
    "GOALS": "总进球",
    "HALF": "半全场",
}


def spf_actual(match):
    if match.actual_home_goals is None: return None
    h, a = match.actual_home_goals, match.actual_away_goals
    if h > a: return "home"
    elif h == a: return "draw"
    else: return "away"

def rq_actual(match, handicap=0):
    if match.actual_home_goals is None: return None
    h, a = match.actual_home_goals + handicap, match.actual_away_goals
    if h > a: return "home"
    elif h == a: return "draw"
    else: return "away"

def score_actual(match):
    if match.actual_home_goals is None: return None
    return f"{match.actual_home_goals}:{match.actual_away_goals}"

def goals_actual(match):
    if match.actual_home_goals is None: return None
    total = match.actual_home_goals + match.actual_away_goals
    return "7+" if total >= 7 else str(total)

def half_actual(match):
    if match.ht_home_goals is None or match.actual_home_goals is None: return None
    def outcome(h, a):
        if h > a: return "主"
        elif h == a: return "平"
        else: return "客"
    return f"{outcome(match.ht_home_goals, match.ht_away_goals)}{outcome(match.actual_home_goals, match.actual_away_goals)}"

ACTUAL_MAP = {
    "SPF": spf_actual,
    "RQ": rq_actual,
    "SCORE": score_actual,
    "GOALS": goals_actual,
    "HALF": half_actual,
}


def clean_probs(raw):
    """清理概率字典，移除非数值键 (如 _mixture)"""
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if isinstance(v, (int, float))}
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            return {k: v for k, v in d.items() if isinstance(v, (int, float))}
        except:
            return {}
    return {}


def classify_confidence(max_prob):
    if max_prob >= 0.60: return "very_high"
    elif max_prob >= 0.50: return "high"
    elif max_prob >= 0.40: return "medium"
    elif max_prob >= 0.33: return "low"
    else: return "very_low"


def classify_odds_range(odds):
    if odds < 1.50: return "1.0-1.5"
    elif odds < 2.00: return "1.5-2.0"
    elif odds < 3.00: return "2.0-3.0"
    elif odds < 5.00: return "3.0-5.0"
    else: return "5.0+"


def get_closing_odds(match):
    h = match.closing_odds_home or match.odds_home
    d = match.closing_odds_draw or match.odds_draw
    a = match.closing_odds_away or match.odds_away
    if h and d and a and h > 1.01 and d > 1.01 and a > 1.01:
        return (h, d, a)
    return None


def validate_mode(db, args, play_types):
    """使用数据库中已有预测做回测 (快速模式)"""
    print(f"\n{'='*70}")
    print(f"  WC Analytics 全量历史回测 — 验证模式 (使用已有预测)")
    print(f"{'='*70}\n")

    all_results = {}
    total_processed = 0

    for pt in play_types:
        start_pt = time.time()
        print(f"\n{'─'*60}")
        print(f"  回测玩法: {pt} ({PLAY_TYPE_LABELS.get(pt, pt)})")
        print(f"{'─'*60}")

        stats = {
            "total": 0, "correct": 0,
            "brier_sum": 0.0, "brier_count": 0,
            "by_league": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_confidence": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_odds_range": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_stage": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_outcome": defaultdict(lambda: {"total": 0, "correct": 0}),
            "max_probs": [], "errors": 0,
        }

        preds = db.query(Prediction).filter(Prediction.play_type == pt.upper()).all()
        pred_map = {p.match_id: p for p in preds}

        finished = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None)
        ).all()

        for match in finished:
            pred = pred_map.get(match.id)
            if not pred: continue

            probs = clean_probs(pred.probabilities)
            if not probs: continue

            # 实际结果
            actual_fn = ACTUAL_MAP.get(pt)
            if not actual_fn: continue

            try:
                if pt == "RQ":
                    actual = actual_fn(match, match.handicap or 0) if hasattr(match, 'handicap') else actual_fn(match, 0)
                elif pt == "HALF":
                    actual = actual_fn(match)
                else:
                    actual = actual_fn(match)
            except Exception:
                continue

            if actual is None: continue

            predicted = max(probs, key=probs.get)
            is_correct = predicted == actual
            max_prob = max(probs.values())

            stats["total"] += 1
            if is_correct: stats["correct"] += 1

            # Brier
            if pt in ("SPF", "RQ"):
                brier = sum((probs.get(k, 1/3) - (1.0 if k == actual else 0.0))**2 for k in ["home", "draw", "away"])
                stats["brier_sum"] += brier
                stats["brier_count"] += 1
            else:
                stats["brier_sum"] += (probs.get(actual, 0.0) - 1.0)**2
                stats["brier_count"] += 1

            stats["max_probs"].append(max_prob)

            comp = match.competition or "unknown"
            stats["by_league"][comp]["total"] += 1
            if is_correct: stats["by_league"][comp]["correct"] += 1

            conf = classify_confidence(max_prob)
            stats["by_confidence"][conf]["total"] += 1
            if is_correct: stats["by_confidence"][conf]["correct"] += 1

            odds_data = get_closing_odds(match)
            if odds_data:
                odds_range = classify_odds_range(odds_data[0])
                stats["by_odds_range"][odds_range]["total"] += 1
                if is_correct: stats["by_odds_range"][odds_range]["correct"] += 1

            stage = match.stage or "group"
            stats["by_stage"][stage]["total"] += 1
            if is_correct: stats["by_stage"][stage]["correct"] += 1

            stats["by_outcome"][actual]["total"] += 1
            if is_correct: stats["by_outcome"][actual]["correct"] += 1

            total_processed += 1

        total = stats["total"]
        correct = stats["correct"]
        accuracy = correct / total if total > 0 else 0.0
        avg_brier = stats["brier_sum"] / stats["brier_count"] if stats["brier_count"] > 0 else 0.0
        avg_max_prob = sum(stats["max_probs"]) / len(stats["max_probs"]) if stats["max_probs"] else 0.0

        high_total = stats["by_confidence"]["high"]["total"] + stats["by_confidence"]["very_high"]["total"]
        high_correct = stats["by_confidence"]["high"]["correct"] + stats["by_confidence"]["very_high"]["correct"]

        pt_time = time.time() - start_pt

        print(f"\n  📊 {pt} 回测结果:")
        print(f"     总场次:      {total:,}")
        print(f"     正确场次:    {correct:,}")
        print(f"     准确率:      {accuracy:.2%}")
        print(f"     Brier Score: {avg_brier:.4f}")
        print(f"     平均最大概率: {avg_max_prob:.4f}")
        if high_total > 0:
            print(f"     高概率(>=50%): {high_correct}/{high_total} = {high_correct/high_total:.2%}")

        if stats["by_confidence"]:
            print(f"\n     ── 按置信度分层 ──")
            for conf in ["very_high", "high", "medium", "low", "very_low"]:
                s = stats["by_confidence"][conf]
                if s["total"] > 0:
                    print(f"       {conf:12s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_outcome"]:
            print(f"\n     ── 按实际赛果分层 ──")
            for outcome in ["home", "draw", "away"]:
                s = stats["by_outcome"][outcome]
                if s["total"] > 0:
                    label = {"home": "主胜", "draw": "平局", "away": "客胜"}.get(outcome, outcome)
                    print(f"       {label:6s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        league_stats = stats["by_league"]
        if league_stats:
            sorted_leagues = sorted(league_stats.items(), key=lambda x: -x[1]["total"])[:10]
            print(f"\n     ── 各联赛 TOP 10 ──")
            for comp, s in sorted_leagues:
                if s["total"] >= 10:
                    print(f"       {comp:20s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_stage"]:
            print(f"\n     ── 按比赛阶段 ──")
            for stage, s in sorted(stats["by_stage"].items(), key=lambda x: -x[1]["total"]):
                print(f"       {stage:10s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_odds_range"]:
            print(f"\n     ── 按赔率区间 ──")
            for odds_r, s in sorted(stats["by_odds_range"].items()):
                if s["total"] > 0:
                    print(f"       {odds_r:10s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        all_results[pt] = {
            "mode": "validate",
            "total": total, "correct": correct,
            "accuracy": round(accuracy, 4),
            "brier": round(avg_brier, 4),
            "avg_max_prob": round(avg_max_prob, 4),
            "high_conf_total": high_total,
            "high_conf_correct": high_correct,
            "high_conf_accuracy": round(high_correct/high_total, 4) if high_total > 0 else 0,
            "by_league": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_league"].items()},
            "by_confidence": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_confidence"].items()},
            "by_outcome": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_outcome"].items()},
            "by_stage": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_stage"].items()},
            "by_odds_range": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_odds_range"].items()},
            "errors": stats["errors"],
            "time_seconds": round(pt_time, 1),
        }

    # 总汇总
    print(f"\n\n{'='*70}")
    print(f"  📋 全量回测总汇总 (验证模式)")
    print(f"{'='*70}")
    print(f"\n  {'玩法':<8s} {'场次':>8s} {'正确':>8s} {'准确率':>8s} {'Brier':>8s} {'高概率准':>8s}")
    print(f"  {'─'*60}")
    for pt, r in all_results.items():
        label = PLAY_TYPE_LABELS.get(pt, pt)
        hc_acc = f"{r['high_conf_accuracy']:.2%}" if r['high_conf_total'] > 0 else "N/A"
        print(f"  {label:<8s} {r['total']:>8,} {r['correct']:>8,} {r['accuracy']:>7.2%} {r['brier']:>8.4f} {hc_acc:>8s}")
    print(f"\n  总处理场次: {total_processed:,}")
    print(f"{'='*70}\n")

    return all_results, total_processed


def fresh_mode(db, args, play_types):
    """重新跑预测引擎做回测 (慢模式)"""
    print(f"\n{'='*70}")
    print(f"  WC Analytics 全量历史回测 — 重算模式 (重新预测)")
    print(f"{'='*70}\n")

    finished = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None)
    ).order_by(Match.kickoff_at.asc()).all()

    print(f"  比赛数: {len(finished):,}")
    print(f"  玩法: {', '.join(play_types)}\n")

    engine = PredictionEngine(db_session=db, use_lr_fusion=not args.no_lr)

    all_results = {}
    total_time = 0

    for pt in play_types:
        start_pt = time.time()
        print(f"\n{'─'*60}")
        print(f"  回测玩法: {pt} ({PLAY_TYPE_LABELS.get(pt, pt)})")
        print(f"{'─'*60}")

        stats = {
            "total": 0, "correct": 0,
            "brier_sum": 0.0, "brier_count": 0,
            "by_league": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_confidence": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_odds_range": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_stage": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_outcome": defaultdict(lambda: {"total": 0, "correct": 0}),
            "max_probs": [], "errors": 0,
        }

        processed = 0
        for match in finished:
            try:
                ctx = build_context_from_match(match)
                result = engine.predict(ctx)
            except Exception as e:
                stats["errors"] += 1
                continue

            actual_fn = ACTUAL_MAP.get(pt)
            if not actual_fn: continue

            try:
                if pt == "RQ":
                    actual = actual_fn(match, match.handicap or 0) if hasattr(match, 'handicap') else actual_fn(match, 0)
                elif pt == "HALF":
                    actual = actual_fn(match)
                else:
                    actual = actual_fn(match)
            except Exception:
                continue

            if actual is None: continue

            if pt == "SPF": probs = result.spf
            elif pt == "RQ": probs = result.rq
            elif pt == "SCORE": probs = result.score
            elif pt == "GOALS": probs = result.goals
            elif pt == "HALF": probs = result.half
            else: continue

            if not probs: continue

            predicted = max(probs, key=probs.get)
            is_correct = predicted == actual
            max_prob = max(probs.values())

            stats["total"] += 1
            if is_correct: stats["correct"] += 1

            if pt in ("SPF", "RQ"):
                brier = sum((probs.get(k, 1/3) - (1.0 if k == actual else 0.0))**2 for k in ["home", "draw", "away"])
                stats["brier_sum"] += brier
                stats["brier_count"] += 1
            else:
                stats["brier_sum"] += (probs.get(actual, 0.0) - 1.0)**2
                stats["brier_count"] += 1

            stats["max_probs"].append(max_prob)

            comp = match.competition or "unknown"
            stats["by_league"][comp]["total"] += 1
            if is_correct: stats["by_league"][comp]["correct"] += 1

            conf = classify_confidence(max_prob)
            stats["by_confidence"][conf]["total"] += 1
            if is_correct: stats["by_confidence"][conf]["correct"] += 1

            odds_data = get_closing_odds(match)
            if odds_data:
                odds_range = classify_odds_range(odds_data[0])
                stats["by_odds_range"][odds_range]["total"] += 1
                if is_correct: stats["by_odds_range"][odds_range]["correct"] += 1

            stage = match.stage or "group"
            stats["by_stage"][stage]["total"] += 1
            if is_correct: stats["by_stage"][stage]["correct"] += 1

            stats["by_outcome"][actual]["total"] += 1
            if is_correct: stats["by_outcome"][actual]["correct"] += 1

            processed += 1
            if processed % 5000 == 0:
                print(f"    进度: {processed:,}/{len(finished):,} ({processed/len(finished):.1%})")

        total = stats["total"]
        correct = stats["correct"]
        accuracy = correct / total if total > 0 else 0.0
        avg_brier = stats["brier_sum"] / stats["brier_count"] if stats["brier_count"] > 0 else 0.0
        avg_max_prob = sum(stats["max_probs"]) / len(stats["max_probs"]) if stats["max_probs"] else 0.0

        high_total = stats["by_confidence"]["high"]["total"] + stats["by_confidence"]["very_high"]["total"]
        high_correct = stats["by_confidence"]["high"]["correct"] + stats["by_confidence"]["very_high"]["correct"]

        pt_time = time.time() - start_pt
        total_time += pt_time

        print(f"\n  📊 {pt} 回测结果:")
        print(f"     总场次:      {total:,}")
        print(f"     正确场次:    {correct:,}")
        print(f"     准确率:      {accuracy:.2%}")
        print(f"     Brier Score: {avg_brier:.4f}")
        print(f"     平均最大概率: {avg_max_prob:.4f}")
        if high_total > 0:
            print(f"     高概率(>=50%): {high_correct}/{high_total} = {high_correct/high_total:.2%}")
        print(f"     预测错误数:   {stats['errors']}")

        if stats["by_confidence"]:
            print(f"\n     ── 按置信度分层 ──")
            for conf in ["very_high", "high", "medium", "low", "very_low"]:
                s = stats["by_confidence"][conf]
                if s["total"] > 0:
                    print(f"       {conf:12s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_outcome"]:
            print(f"\n     ── 按实际赛果分层 ──")
            for outcome in ["home", "draw", "away"]:
                s = stats["by_outcome"][outcome]
                if s["total"] > 0:
                    label = {"home": "主胜", "draw": "平局", "away": "客胜"}.get(outcome, outcome)
                    print(f"       {label:6s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        league_stats = stats["by_league"]
        if league_stats:
            sorted_leagues = sorted(league_stats.items(), key=lambda x: -x[1]["total"])[:10]
            print(f"\n     ── 各联赛 TOP 10 ──")
            for comp, s in sorted_leagues:
                if s["total"] >= 10:
                    print(f"       {comp:20s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_stage"]:
            print(f"\n     ── 按比赛阶段 ──")
            for stage, s in sorted(stats["by_stage"].items(), key=lambda x: -x[1]["total"]):
                print(f"       {stage:10s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        if stats["by_odds_range"]:
            print(f"\n     ── 按赔率区间 ──")
            for odds_r, s in sorted(stats["by_odds_range"].items()):
                if s["total"] > 0:
                    print(f"       {odds_r:10s}: {s['correct']:>5}/{s['total']:>5} = {s['correct']/s['total']:.2%}")

        all_results[pt] = {
            "mode": "fresh",
            "total": total, "correct": correct,
            "accuracy": round(accuracy, 4),
            "brier": round(avg_brier, 4),
            "avg_max_prob": round(avg_max_prob, 4),
            "high_conf_total": high_total,
            "high_conf_correct": high_correct,
            "high_conf_accuracy": round(high_correct/high_total, 4) if high_total > 0 else 0,
            "by_league": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_league"].items()},
            "by_confidence": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_confidence"].items()},
            "by_outcome": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_outcome"].items()},
            "by_stage": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_stage"].items()},
            "by_odds_range": {k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"]/v["total"], 4)} for k, v in stats["by_odds_range"].items()},
            "errors": stats["errors"],
            "time_seconds": round(pt_time, 1),
        }

    print(f"\n\n{'='*70}")
    print(f"  📋 全量回测总汇总 (重算模式)")
    print(f"{'='*70}")
    print(f"\n  {'玩法':<8s} {'场次':>8s} {'正确':>8s} {'准确率':>8s} {'Brier':>8s} {'耗时':>8s}")
    print(f"  {'─'*60}")
    for pt, r in all_results.items():
        label = PLAY_TYPE_LABELS.get(pt, pt)
        print(f"  {label:<8s} {r['total']:>8,} {r['correct']:>8,} {r['accuracy']:>7.2%} {r['brier']:>8.4f} {r['time_seconds']:>7.0f}s")
    print(f"\n  总耗时: {total_time:.0f}s")
    print(f"{'='*70}\n")

    return all_results, len(finished)


def main():
    parser = argparse.ArgumentParser(description="全量历史回测")
    parser.add_argument("--play-types", nargs="+", default=None,
                        help="要回测的玩法类型 (默认: 全部 SPF RQ SCORE GOALS HALF)")
    parser.add_argument("--fresh", action="store_true",
                        help="重算模式: 重新跑预测引擎 (慢)")
    parser.add_argument("--no-lr", action="store_true",
                        help="关闭逻辑回归融合 (仅重算模式)")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 JSON 报告路径")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        play_types = args.play_types if args.play_types else ["SPF", "RQ", "SCORE", "GOALS", "HALF"]

        if args.fresh:
            all_results, total = fresh_mode(db, args, play_types)
        else:
            all_results, total = validate_mode(db, args, play_types)

        # 保存 JSON
        if args.output:
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "fresh" if args.fresh else "validate",
                "total_matches": total,
                "results": all_results,
            }
            Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False))
            print(f"  报告已保存到: {args.output}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
