#!/usr/bin/env python3
"""
全量模型训练脚本 — 一次性训练所有子模型
用法: cd backend && source venv/bin/activate && python train_all.py
"""
import sys
import os
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "scripts")

from utils.logger import get_logger
logger = get_logger("train_all")

TRAINING_ORDER = [
    ("Fusion LR (逻辑回归融合)", "fusion_train"),
    ("NN (残差神经网络)", "nn_train"),
    ("Draw Classifier (平局分类器)", "draw_classifier"),
    ("Halftime (半场子模型)", "halftime"),
    ("Score (比分子模型)", "score"),
    ("Handicap (让球子模型)", "handicap"),
]


def fusion_train():
    from fusion.validate_deploy import train_with_validation
    result = train_with_validation(
        l1_penalty=0.001,
        class_weight={0: 0.8, 1: 1.5, 2: 0.8},
        val_ratio=0.1,
    )
    logger.info(f"Fusion LR: deployed={result.get('deployed')}, delta_brier={result.get('delta_brier', 'N/A')}")


def nn_train():
    from core.residual_nn import StackingTrainer
    from database.config import get_db
    db = next(get_db())
    try:
        trainer = StackingTrainer(db_session=db)
        result = trainer.train()
        logger.info(f"NN: {result}")
    finally:
        db.close()


def draw_classifier():
    from core.draw_classifier import draw_classifier_train_job
    draw_classifier_train_job()


def halftime():
    from sub_model_halftime import halftime_train_job
    halftime_train_job()


def score():
    from sub_model_score import score_train_job
    score_train_job()


def handicap():
    from sub_model_handicap import handicap_train_job
    handicap_train_job()


def main():
    logger.info("=" * 60)
    logger.info("全量模型训练开始")
    logger.info("=" * 60)

    total_start = time.time()
    funcs = {
        "fusion_train": fusion_train,
        "nn_train": nn_train,
        "draw_classifier": draw_classifier,
        "halftime": halftime,
        "score": score,
        "handicap": handicap,
    }

    for name, func_name in TRAINING_ORDER:
        logger.info(f"\n{'='*40}")
        logger.info(f"训练: {name}")
        logger.info(f"{'='*40}")
        try:
            start = time.time()
            funcs[func_name]()
            elapsed = time.time() - start
            logger.info(f"✅ {name} 完成, 耗时 {elapsed:.0f}s")
        except Exception as e:
            logger.error(f"❌ {name} 失败: {e}", exc_info=True)

    total = time.time() - total_start
    logger.info(f"\n{'='*60}")
    logger.info(f"全部训练完成, 总耗时 {total:.0f}s")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
