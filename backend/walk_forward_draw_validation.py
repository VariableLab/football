"""Walk-forward validation for draw calibration.

This script validates a realistic workflow:
  train calibration params on past matches only, then apply them to future
  matches. It does not rewrite predictions; it reports calibrated metrics and
  saves the latest recommended params.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from draw_calibrator import (
    DEFAULT_DRAW_PARAMS,
    DrawFeatures,
    apply_draw_calibration,
    evaluate_rows,
    market_probabilities,
    tune_params,
)


DEFAULT_DB = os.path.join(os.path.dirname(__file__), "database.sqlite")
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(__file__), "data", "draw_calibration")


def parse_probabilities(raw) -> Dict[str, float]:
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def load_rows(db_path: str, league: Optional[str] = None, with_odds_only: bool = False) -> List[Dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT
                m.id,
                m.kickoff_at,
                m.actual_outcome,
                m.stage,
                ht.elo AS home_elo,
                at.elo AS away_elo,
                ht.avg_xg AS home_xg,
                at.avg_xg AS away_xg,
                ht.avg_goals_scored AS home_goals_avg,
                at.avg_goals_scored AS away_goals_avg,
                m.closing_odds_home,
                m.closing_odds_draw,
                m.closing_odds_away,
                m.odds_home,
                m.odds_draw,
                m.odds_away,
                p.probabilities
            FROM matches m
            JOIN predictions p ON p.match_id = m.id AND p.play_type = 'SPF'
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.status = 'FINISHED'
              AND m.actual_outcome IS NOT NULL
              AND m.kickoff_at IS NOT NULL
        """
        params: List[str] = []
        if league:
            sql += " AND m.competition LIKE ?"
            params.append(f"{league}%")
        if with_odds_only:
            sql += """
              AND (
                (m.closing_odds_home > 1.01 AND m.closing_odds_draw > 1.01 AND m.closing_odds_away > 1.01)
                OR (m.odds_home > 1.01 AND m.odds_draw > 1.01 AND m.odds_away > 1.01)
              )
            """
        sql += " ORDER BY m.kickoff_at, m.id"

        rows: List[Dict] = []
        for r in conn.execute(sql, params):
            market = market_probabilities(
                r["closing_odds_home"] or r["odds_home"],
                r["closing_odds_draw"] or r["odds_draw"],
                r["closing_odds_away"] or r["odds_away"],
            )
            home_xg = r["home_xg"] or r["home_goals_avg"] or 1.3
            away_xg = r["away_xg"] or r["away_goals_avg"] or 1.3
            rows.append(
                {
                    "match_id": r["id"],
                    "kickoff_at": r["kickoff_at"],
                    "actual_outcome": r["actual_outcome"],
                    "probabilities": parse_probabilities(r["probabilities"]),
                    "draw_features": DrawFeatures(
                        elo_diff=(r["home_elo"] or 1500) - (r["away_elo"] or 1500),
                        xg_diff=home_xg - away_xg,
                        market_draw_prob=market["draw"] if market else None,
                        is_knockout=r["stage"] not in (None, "", "group"),
                    ),
                }
            )
        return rows
    finally:
        conn.close()


def run_walk_forward(
    rows: List[Dict],
    min_train: int,
    train_window: int,
    test_window: int,
    step: int,
) -> Dict:
    folds = []
    test_baseline_totals: List[Dict] = []
    test_calibrated_totals: List[Dict] = []

    start = min_train
    while start < len(rows):
        train_start = max(0, start - train_window)
        train_rows = rows[train_start:start]
        test_rows = rows[start : min(start + test_window, len(rows))]
        if len(train_rows) < min_train or not test_rows:
            break

        params, train_metrics = tune_params(train_rows)
        baseline = evaluate_rows(test_rows)
        calibrated = evaluate_rows(test_rows, params)
        test_baseline_totals.append(baseline)
        test_calibrated_totals.append(calibrated)

        folds.append(
            {
                "train_start": train_rows[0]["kickoff_at"],
                "train_end": train_rows[-1]["kickoff_at"],
                "test_start": test_rows[0]["kickoff_at"],
                "test_end": test_rows[-1]["kickoff_at"],
                "train_size": len(train_rows),
                "test_size": len(test_rows),
                "params": params.to_dict(),
                "train_metrics": _round_metrics(train_metrics),
                "baseline": _round_metrics(baseline),
                "calibrated": _round_metrics(calibrated),
            }
        )
        start += step

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "folds": len(folds),
        "baseline": _round_metrics(_weighted_average(test_baseline_totals)),
        "calibrated": _round_metrics(_weighted_average(test_calibrated_totals)),
        "latest_params": folds[-1]["params"] if folds else None,
        "fold_results": folds,
    }
    accepted = (
        summary["calibrated"]["accuracy"] > summary["baseline"]["accuracy"]
        and summary["calibrated"]["brier"] <= summary["baseline"]["brier"]
    )
    summary["accepted"] = accepted
    if not accepted:
        summary["latest_params"] = DEFAULT_DRAW_PARAMS.to_dict()
    return summary


def _weighted_average(items: List[Dict]) -> Dict:
    total = sum(item["total"] for item in items)
    if total <= 0:
        return evaluate_rows([])
    out = {"total": total}
    out["correct"] = sum(item["correct"] for item in items)
    out["accuracy"] = out["correct"] / total
    for key in ("brier", "log_loss", "draw_prediction_rate", "actual_draw_rate"):
        out[key] = sum(item[key] * item["total"] for item in items) / total
    return out


def _round_metrics(metrics: Dict) -> Dict:
    rounded = {}
    for key, val in metrics.items():
        if isinstance(val, float):
            rounded[key] = round(val, 6)
        else:
            rounded[key] = val
    return rounded


def save_results(summary: Dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    metrics_path = os.path.join(out_dir, "walk_forward_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    if summary.get("latest_params"):
        params_path = os.path.join(out_dir, "params.json")
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(summary["latest_params"], f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward draw calibration validation")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--league", default=None)
    parser.add_argument("--with-odds", action="store_true")
    parser.add_argument("--min-train", type=int, default=3000)
    parser.add_argument("--train-window", type=int, default=8000)
    parser.add_argument("--test-window", type=int, default=1000)
    parser.add_argument("--step", type=int, default=1000)
    args = parser.parse_args()

    rows = load_rows(args.db, league=args.league, with_odds_only=args.with_odds)
    summary = run_walk_forward(
        rows,
        min_train=args.min_train,
        train_window=args.train_window,
        test_window=args.test_window,
        step=args.step,
    )
    save_results(summary, args.out_dir)

    print("=" * 72)
    print("  Walk-forward draw calibration")
    print("=" * 72)
    print(f"  rows={summary['rows']} folds={summary['folds']}")
    print(
        "  baseline   "
        f"acc={summary['baseline']['accuracy']:.2%} "
        f"brier={summary['baseline']['brier']:.4f} "
        f"logloss={summary['baseline']['log_loss']:.4f} "
        f"draw_pred={summary['baseline']['draw_prediction_rate']:.2%}"
    )
    print(
        "  calibrated "
        f"acc={summary['calibrated']['accuracy']:.2%} "
        f"brier={summary['calibrated']['brier']:.4f} "
        f"logloss={summary['calibrated']['log_loss']:.4f} "
        f"draw_pred={summary['calibrated']['draw_prediction_rate']:.2%}"
    )
    print(f"  saved={args.out_dir}")
    print(f"  accepted={summary['accepted']}")
    if summary.get("latest_params"):
        print(f"  latest_params={json.dumps(summary['latest_params'], ensure_ascii=False)}")
    print("=" * 72)


if __name__ == "__main__":
    main()
