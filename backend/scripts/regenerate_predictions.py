"""
用最新的模型参数重新生成所有预测。

支持三种模式:
1. --scheduled (默认): 只重生成 SCHEDULED 比赛
2. --all: 重生成所有比赛（含 FINISHED，用于验证）
3. --finished: 只重生成 FINISHED 比赛（用于验证/校准）

用法:
    cd backend && python3 regenerate_predictions.py
    cd backend && python3 regenerate_predictions.py --all
    cd backend && python3 regenerate_predictions.py --finished
"""

import argparse
import hashlib
import json

from sqlalchemy.orm import Session
from database.models import SessionLocal, Match, MatchStatus, Prediction
from core.prediction_engine import PredictionEngine, build_context_from_match
from utils.logger import get_logger

logger = get_logger("regenerate_predictions")


def _compute_checksum(ctx) -> str:
    data = {
        "home_elo": ctx.home_team.elo,
        "away_elo": ctx.away_team.elo,
        "home_form": ctx.home_team.form_factor,
        "away_form": ctx.away_team.form_factor,
        "home_xg": ctx.home_team.avg_xg,
        "away_xg": ctx.away_team.avg_xg,
        "odds_home": ctx.odds_home,
        "odds_draw": ctx.odds_draw,
        "odds_away": ctx.odds_away,
        "closing_h": ctx.closing_odds_home,
        "closing_d": ctx.closing_odds_draw,
        "closing_a": ctx.closing_odds_away,
        "is_knockout": ctx.is_knockout,
    }
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def regenerate_matches(db: Session, matches: list, label: str = "") -> int:
    created = 0
    failed = 0

    for match in matches:
        db.query(Prediction).filter(
            Prediction.match_id == match.id,
            Prediction.model_version.in_(["v2.0", "v3.0", "v3.0_shadow", "v3.0_classic", "v4.0"])
        ).delete()

        try:
            ctx = build_context_from_match(match)
            if ctx is None:
                failed += 1
                continue
            engine = PredictionEngine(db_session=db)
            result = engine.predict(ctx)
            checksum = _compute_checksum(ctx)

            for payload in result.to_db_payload():
                pred = Prediction(
                    match_id=match.id,
                    play_type=payload["play_type"],
                    probabilities=payload["probabilities"],
                    input_checksum=checksum,
                    model_version=payload["model_version"],
                )
                db.add(pred)
            created += 1

            if created <= 3 or created % 500 == 0:
                logger.info(
                    f"[{match.match_code}] SPF: H={result.spf.get('home', 0):.1%} "
                    f"D={result.spf.get('draw', 0):.1%} A={result.spf.get('away', 0):.1%}"
                )
        except Exception as e:
            failed += 1
            if failed <= 5:
                logger.error(f"[{match.match_code}] Prediction failed: {e}")

    db.commit()
    logger.info(f"{label} Regenerated {created}/{len(matches)} matches ({failed} failed)")
    return created


def regenerate_all(db: Session) -> int:
    matches = db.query(Match).filter(Match.status == MatchStatus.SCHEDULED).all()
    return regenerate_matches(db, matches, "[scheduled]")


def regenerate_finished(db: Session) -> int:
    matches = db.query(Match).filter(Match.status == MatchStatus.FINISHED).all()
    return regenerate_matches(db, matches, "[finished]")


def regenerate_everything(db: Session) -> int:
    matches = db.query(Match).all()
    return regenerate_matches(db, matches, "[all]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regenerate predictions")
    parser.add_argument("--all", action="store_true", help="Regenerate ALL matches")
    parser.add_argument("--finished", action="store_true", help="Regenerate FINISHED matches only")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.all:
            count = regenerate_everything(db)
        elif args.finished:
            count = regenerate_finished(db)
        else:
            count = regenerate_all(db)
        print(f"Done. Created predictions for {count} matches.")
    finally:
        db.close()
