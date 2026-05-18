"""
融合层 — 逻辑回归融合 + 权重学习 + 残差修正

模块:
 logistic_fusion.py — 多项式逻辑回归融合器 (L-BFGS-B + L1)
 fusion_trainer.py — 训练管线 (DB → Features → LR → 权重保存)

注意：FusionTrainer 延迟导入以避免循环依赖
 (fusion_trainer → prediction_engine → fusion 的循环)
"""
from fusion.logistic_fusion import (
 LogisticFusionWeights,
 LogisticFusionTrainer,
 cross_validate_lambda,
)


def __getattr__(name):
    """延迟导入 FusionTrainer，避免循环依赖"""
    if name == "FusionTrainer":
        from fusion.fusion_trainer import FusionTrainer
        return FusionTrainer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
 "LogisticFusionWeights",
 "LogisticFusionTrainer",
 "cross_validate_lambda",
 "FusionTrainer",
]
