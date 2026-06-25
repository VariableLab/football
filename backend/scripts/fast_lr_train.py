#!/usr/bin/env python3
"""快速训练 Fusion LR — 仅用 closing_odds + 球队统计, 避免逐场 DB 查询。

用法:
    cd backend && PYTHONPATH=. ./venv/bin/python scripts/fast_lr_train.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from database.models import SessionLocal, Match, MatchStatus
from core.models.elo import EloModel
from core.models.poisson import PoissonModel
from core.models.market import MarketModel
from core.models.player_adjustment import PlayerAdjustmentModel
from features.feature_builder import FeatureBuilder
from fusion.logistic_fusion import LogisticFusionTrainer, LogisticFusionWeights
from utils.logger import get_logger

logger = get_logger("fast_lr_train")

FRIENDLY_KEYWORDS = ("friendly", "warm-up", "exhibition", "invitational")
BAD_OUTCOMES = ("abandoned", "unknown", "cancelled", "postponed", "")


def main():
    s = SessionLocal()
    try:
        # 查询有 closing_odds 的已完成比赛
        matches = s.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
            Match.closing_odds_home.isnot(None),
            Match.closing_odds_draw.isnot(None),
            Match.closing_odds_away.isnot(None),
        ).all()
        logger.info(f"Loaded {len(matches)} matches with closing odds")

        # 过滤
        valid = []
        for m in matches:
            comp = (m.competition or "").lower()
            if any(kw in comp for kw in FRIENDLY_KEYWORDS):
                continue
            outcome = (m.actual_outcome or "").lower()
            if outcome in BAD_OUTCOMES:
                continue
            valid.append(m)
        logger.info(f"After quality gate: {len(valid)} matches")

        # 构建特征 (不查 Form/H2H, 只用球队统计)
        from core.prediction_engine import build_context_from_match
        feature_builder = FeatureBuilder(use_interactions=False)  # 禁用交互特征加速
        Xl, yl = [], []
        o2i = {"home": 0, "draw": 1, "away": 2}

        t0 = time.time()
        for i, m in enumerate(valid):
            if i % 2000 == 0:
                logger.info(f"Building features: {i}/{len(valid)} ({time.time()-t0:.0f}s)")
            try:
                yv = o2i.get(m.actual_outcome)
                if yv is None:
                    continue
                if m.home_team is None or m.away_team is None:
                    continue

                ctx = build_context_from_match(m)
                e = EloModel.predict(ctx)
                p = PoissonModel.predict(ctx)
                pl = PlayerAdjustmentModel.predict(ctx)
                mk = MarketModel.predict(ctx)
                # 用默认 form/h2h
                xv = feature_builder.build(e, p, pl, mk, None, None, ctx)
                Xl.append(xv)
                yl.append(yv)
            except Exception:
                continue

        elapsed = time.time() - t0
        logger.info(f"Built {len(Xl)} features in {elapsed:.0f}s ({len(Xl)/elapsed:.0f} matches/sec)")

        if len(Xl) < 100:
            logger.warning("Too few features")
            return

        X = np.array(Xl, dtype=np.float64)
        y = np.array(yl, dtype=np.int64)
        logger.info(f"Feature matrix: {X.shape}, Labels: {y.shape}")
        logger.info(f"Label distribution: home={np.sum(y==0)}, draw={np.sum(y==1)}, away={np.sum(y==2)}")

        # 时间切分: 80/20
        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        logger.info(f"Train: {len(X_train)}, Val: {len(X_val)}")

        # 训练
        logger.info("Training LogisticFusion...")
        trainer = LogisticFusionTrainer(l1_penalty=0.001, max_iter=1000,
                                        class_weight={0: 0.8, 1: 1.5, 2: 0.8})
        weights = trainer.fit(X_train, y_train, league="global_optimized")

        # 验证
        val_probs = weights.predict(X_val)
        val_acc = float(np.mean(np.argmax(val_probs, axis=1) == y_val))
        logger.info(f"Val accuracy: {val_acc:.4f}")
        logger.info(f"Train accuracy: {weights.accuracy:.4f}")
        logger.info(f"Cross-entropy: {weights.cross_entropy:.4f}")

        # 保存
        path = weights.save()
        logger.info(f"Weights saved to {path}")

    finally:
        s.close()


if __name__ == "__main__":
    main()
