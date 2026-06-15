"""一键全量重训练脚本 - 用于在防泄露重构后刷新模型权重"""
import os
import sys
import logging
import traceback

# 路径初始化
_cur_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_cur_dir)
sys.path.append(_backend_root)
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_backend_root, d))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("retrain_all")

def main():
    logger.info("🚀 开始全量重训管线...")
    
    # 1. 训练 Layer 2 逻辑回归融合层
    try:
        logger.info("📅 Step 1/2: 正在拟合逻辑回归全局融合权重 (Layer 2)...")
        from fusion.fusion_trainer import FusionTrainer
        trainer = FusionTrainer(limit=None)
        w = trainer.train_global(class_weight={0: 1.0, 1: 3.0, 2: 1.0})
        if w:
            path = w.save()
            logger.info(f"✅ LR 融合权重拟合完成! 准确率: {w.accuracy:.2%}, 样本量: {w.sample_count}, 保存路径: {path}")
        else:
            logger.error("❌ LR 融合权重训练失败: 未返回有效权重")
            return
    except Exception as e:
        logger.error(f"❌ Step 1 (LR) 发生异常: {e}")
        traceback.print_exc()
        return

    # 2. 训练 Layer 3 Stacking 神经网络残差修正层
    try:
        logger.info("🧠 Step 2/2: 正在训练 Stacking 神经网络纠偏网络 (Layer 3)...")
        from core.residual_nn import StackingTrainer
        from database.models import SessionLocal
        s = SessionLocal()
        st_trainer = StackingTrainer(db_session=s)
        stats = st_trainer.train()
        s.close()
        if stats:
            logger.info(f"✅ Stacking 神经网络训练完成! 最佳验证 Loss: {stats['best_loss']:.4f}, 保存于 data/weights/nn/")
        else:
            logger.error("❌ Stacking 神经网络训练失败")
    except Exception as e:
        logger.error(f"❌ Step 2 (StackingNet) 发生异常: {e}")
        traceback.print_exc()

    logger.info("🎉 全量重训管线执行完毕！模型已升级为最新的防泄露、对齐后的最佳参数状态。")

if __name__ == "__main__":
    main()
