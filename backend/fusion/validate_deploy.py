"""
LR 权重 A/B 验证后部署

1. 切分验证集（按时间，最近 10% 的比赛）
2. 在新权重上评估 Brier / Accuracy
3. 加载当前生产权重，同验证集评估
4. 新权重更优才部署，否则告警回滚
5. 写入版本元数据（供日后追踪）
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import numpy as np

from fusion.fusion_trainer import FusionTrainer
from fusion.logistic_fusion import LogisticFusionTrainer, LogisticFusionWeights
from utils.logger import get_logger

logger = get_logger("validate_deploy")

WEIGHTS_DIR = "data/weights/lr"
PRODUCTION_WEIGHT_PATH = os.path.join(WEIGHTS_DIR, "global_v1_2026-05-15.json")
VALIDATION_META_PATH = os.path.join(WEIGHTS_DIR, "validation_meta.json")


def _safe_log_loss(y_true, y_pred):
    """安全计算 log_loss，sklearn 不可用时用 numpy 近似"""
    try:
        from sklearn.metrics import log_loss
        return float(log_loss(y_true, y_pred, labels=[0, 1, 2]))
    except ImportError:
        # Fallback: multiclass cross-entropy
        eps = 1e-15
        y_pred_clipped = np.clip(y_pred, eps, 1 - eps)
        n = len(y_true)
        y_onehot = np.zeros((n, y_pred.shape[1]))
        y_onehot[np.arange(n), y_true] = 1.0
        return float(-np.mean(np.sum(y_onehot * np.log(y_pred_clipped), axis=1)))


def _find_latest_weight() -> str:
    """找到最新的生产权重文件"""
    import glob
    files = sorted(glob.glob(os.path.join(WEIGHTS_DIR, "*.json")), reverse=True)
    meta_only = {"validation_meta.json"}
    for f in files:
        base = os.path.basename(f)
        if base not in meta_only and base != os.path.basename(PRODUCTION_WEIGHT_PATH):
            return f
    return PRODUCTION_WEIGHT_PATH  # fallback to hardcoded path


def _brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """计算多分类 Brier Score"""
    n = len(y_true)
    if n == 0:
        return 1.0
    y_onehot = np.zeros((n, 3))
    y_onehot[np.arange(n), y_true] = 1.0
    return float(np.mean(np.sum((y_pred - y_onehot) ** 2, axis=1)))


def _time_split(X: np.ndarray, y: np.ndarray, val_ratio: float = 0.15
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """按时间顺序切分（最近 val_ratio 作为验证集，15% ≈ 5000+ 样本）"""
    n = len(X)
    split = int(n * (1 - val_ratio))
    return X[:split], X[split:], y[:split], y[split:]


def train_with_validation(
    l1_penalty: float = 0.001,
    class_weight: Optional[Dict[int, float]] = None,
    val_ratio: float = 0.1,
    dry_run: bool = False,
) -> Dict:
    """训练 + A/B 验证部署

    Returns:
        部署结果字典: {deployed, old_brier, new_brier, accuracy, ...}
    """
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "deployed": False,
        "rolled_back": False,
    }

    # Step 1: 构建特征
    logger.info("[AB] Building features...")
    t0 = time.time()
    trainer = FusionTrainer(limit=None)
    X, y = trainer._build()
    logger.info(f"[AB] {len(X)} samples, {X.shape[1]} dims, {time.time()-t0:.0f}s")

    # Step 2: 按时间切分
    X_train, X_val, y_train, y_val = _time_split(X, y, val_ratio)
    logger.info(f"[AB] Train: {len(X_train)}, Val: {len(X_val)}")

    # Step 3: 训练
    logger.info("[AB] Training LR...")
    t0 = time.time()
    lr = LogisticFusionTrainer(l1_penalty=l1_penalty, max_iter=1000,
                                class_weight=class_weight)
    new_weights = lr.fit(X_train, y_train, league="global")
    logger.info(f"[AB] Training done in {time.time()-t0:.0f}s")
    logger.info(f"     Train acc={new_weights.accuracy:.4f}")

    # Step 4: 验证新权重
    new_probs = new_weights.predict(X_val)
    new_brier = _brier_score(y_val, new_probs)
    new_acc = float(np.mean(np.argmax(new_probs, axis=1) == y_val))
    new_ce = float(_safe_log_loss(y_val, new_probs))
    logger.info(f"[AB] New weights  | Val acc={new_acc:.4f} Brier={new_brier:.4f} CE={new_ce:.4f}")

    result["new"] = {"accuracy": new_acc, "brier": new_brier, "cross_entropy": new_ce}

    # Step 5: 加载旧权重并验证
    current_weight_path = _find_latest_weight()
    old_weights = LogisticFusionWeights.load(current_weight_path)
    if old_weights is None:
        logger.info("[AB] No existing weights found, deploying directly")
        if not dry_run:
            save_and_tag(new_weights)
            result["deployed"] = True
        return result

    old_probs = old_weights.predict(X_val)
    old_brier = _brier_score(y_val, old_probs)
    old_acc = float(np.mean(np.argmax(old_probs, axis=1) == y_val))
    old_ce = float(_safe_log_loss(y_val, old_probs))
    logger.info(f"[AB] Current weights | Val acc={old_acc:.4f} Brier={old_brier:.4f} CE={old_ce:.4f}")

    result["old"] = {"accuracy": old_acc, "brier": old_brier, "cross_entropy": old_ce}
    result["delta_brier"] = round(old_brier - new_brier, 5)

    # Step 6: A/B 决断
    IMPROVEMENT_THRESHOLD = -0.002  # Brier 改善至少 0.002 才部署

    if new_brier < old_brier + IMPROVEMENT_THRESHOLD:
        logger.info(f"[AB] New weights BETTER (delta={result['delta_brier']:+f}), deploying")
        if not dry_run:
            save_and_tag(new_weights)
            result["deployed"] = True
            result["decision"] = "deploy_new"
    else:
        logger.warning(f"[AB] New weights NOT better (delta={result['delta_brier']:+f}), keeping old")
        result["deployed"] = False
        result["decision"] = "keep_old"

    return result


def save_and_tag(weights: LogisticFusionWeights) -> str:
    """保存权重 + 写入验证元数据"""
    path = weights.save()
    meta = {
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "accuracy": weights.accuracy,
        "cross_entropy": weights.cross_entropy,
        "sample_count": weights.sample_count,
        "file": os.path.basename(path),
    }
    with open(VALIDATION_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"[AB] Deployed: {path}")
    return path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    args = parser.parse_args()

    result = train_with_validation(
        l1_penalty=0.001,
        class_weight={0: 0.8, 1: 1.5, 2: 0.8},
        val_ratio=args.val_ratio,
        dry_run=args.dry_run,
    )

    print(f"\n{'[DRY RUN]' if args.dry_run else '[DEPLOY]'} Result:")
    print(f"  Deployed: {result.get('deployed', False)}")
    if "old" in result:
        print(f"  Old Brier: {result['old']['brier']:.4f} (acc={result['old']['accuracy']:.4f})")
    print(f"  New Brier: {result['new']['brier']:.4f} (acc={result['new']['accuracy']:.4f})")
    if "delta_brier" in result:
        print(f"  Delta: {result['delta_brier']:+f}")
    print(f"  Decision: {result.get('decision', 'N/A')}")
