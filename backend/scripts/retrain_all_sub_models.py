#!/usr/bin/env python3
"""
全量子模型重新训练脚本

统一训练:
1. ScoreNet (比分预测) — 减少类别数，增加数据量
2. HalftimeNet (半全场预测) — 9分类
3. HandicapNet (让球预测) — 3分类
4. BetNN (投注价值) — 集成到主管线

所有模型使用相同的训练数据构建逻辑，确保无时序泄漏。
"""
import sys
import os
import json
import numpy as np

# Add backend to path

from utils.logger import get_logger

logger = get_logger("retrain_all")


def retrain_score_model():
    """重新训练比分预测模型 — 关键改动: 减少类别, 增加样本"""
    logger.info("=" * 60)
    logger.info("Retraining ScoreNet (比分预测)")
    logger.info("=" * 60)

    # 导入训练器
    from scripts.sub_model_score import ScoreTrainer

    trainer = ScoreTrainer()
    result = trainer.train()

    if result:
        logger.info(f"ScoreNet training complete:")
        logger.info(f"  Samples: {result['samples']}")
        logger.info(f"  Best val_accuracy: {result['best_val_accuracy']:.1%}")
        logger.info(f"  Final val_accuracy: {result['final_val_accuracy']:.1%}")
        logger.info(f"  Epochs: {result['epochs_trained']}")
        return result
    else:
        logger.warning("ScoreNet training failed — insufficient data")
        return None


def retrain_halftime_model():
    """重新训练半全场预测模型"""
    logger.info("=" * 60)
    logger.info("Retraining HalftimeNet (半全场预测)")
    logger.info("=" * 60)

    from scripts.sub_model_halftime import HalftimeTrainer

    trainer = HalftimeTrainer()
    result = trainer.train()

    if result:
        logger.info(f"HalftimeNet training complete:")
        logger.info(f"  Samples: {result['samples']}")
        logger.info(f"  Best val_accuracy: {result['best_val_accuracy']:.1%}")
        logger.info(f"  Final val_accuracy: {result['final_val_accuracy']:.1%}")
        return result
    else:
        logger.warning("HalftimeNet training failed")
        return None


def retrain_handicap_model():
    """重新训练让球预测模型"""
    logger.info("=" * 60)
    logger.info("Retraining HandicapNet (让球预测)")
    logger.info("=" * 60)

    from scripts.sub_model_handicap import HandicapTrainer

    trainer = HandicapTrainer()
    result = trainer.train()

    if result:
        logger.info(f"HandicapNet training complete:")
        logger.info(f"  Samples: {result['samples']}")
        logger.info(f"  Best val_accuracy: {result['best_val_accuracy']:.1%}")
        logger.info(f"  Final val_accuracy: {result['final_val_accuracy']:.1%}")
        return result
    else:
        logger.warning("HandicapNet training failed")
        return None


def retrain_bet_nn():
    """重新训练 BetNN — 同时集成到预测管线"""
    logger.info("=" * 60)
    logger.info("Retraining BetNN (投注价值分类)")
    logger.info("=" * 60)

    from core.bet_nn import BetNetTrainer

    trainer = BetNetTrainer()
    result = trainer.train()

    if result:
        logger.info(f"BetNN training complete:")
        logger.info(f"  Samples: {result['samples']}")
        logger.info(f"  Best val_accuracy: {result['best_val_accuracy']:.1%}")
        logger.info(f"  Final val_accuracy: {result['final_val_accuracy']:.1%}")
        return result
    else:
        logger.warning("BetNN training failed")
        return None


def main():
    """主训练流程"""
    logger.info("Starting full sub-model retraining pipeline")
    logger.info(f"Database: database.sqlite")

    results = {}

    # 1. ScoreNet — 最关键，先跑
    results["score"] = retrain_score_model()

    # 2. HalftimeNet
    results["halftime"] = retrain_halftime_model()

    # 3. HandicapNet
    results["handicap"] = retrain_handicap_model()

    # 4. BetNN
    results["bet_nn"] = retrain_bet_nn()

    # Summary
    logger.info("=" * 60)
    logger.info("Training Summary")
    logger.info("=" * 60)
    for name, result in results.items():
        if result:
            acc = result.get('best_val_accuracy', 0)
            logger.info(f"  {name}: val_acc={acc:.1%}, samples={result.get('samples', '?')}")
        else:
            logger.info(f"  {name}: FAILED")

    # Save summary
    summary = {
        "trained_at": str(np.datetime64('now')),
        "models": {
            name: {
                "status": "success" if r else "failed",
                "val_accuracy": r.get("best_val_accuracy", 0) if r else 0,
                "samples": r.get("samples", 0) if r else 0,
            }
            for name, r in results.items()
        }
    }
    with open("data/strategy/monitor/retrain_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Summary saved to data/strategy/monitor/retrain_summary.json")
    return results


if __name__ == "__main__":
    main()
