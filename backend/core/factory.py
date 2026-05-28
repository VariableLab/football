"""
ProQuant 量化工厂 - 全自动生产流水线 (MLOps)

功能:
1. 监听外部信号 (T+0)，启动同步任务
2. 触发预测引擎热更新
3. 自动化复盘、模型绩效审计与重训逻辑
"""
import logging
import asyncio
from datetime import datetime
from ingestion.zgzcw_jc_sync import sync_jc_matches
from ingestion.jingcai_quant_collector import run_quant_collector_job
from monitor.model_audit import daily_audit_job
from core.residual_nn import StackingTrainer

logger = logging.getLogger("quant_factory")

class QuantFactory:
    """
    [意图识别层] 量化工厂：全自动 MLOps 调度中心。
    """
    def __init__(self):
        self.is_running = False

    async def run_cycle(self, db_session=None):
        """主生产循环"""
        logger.info("🏭 ProQuant Factory: Starting production cycle...")
        
        # 1. 数据同步 (Ingestion)
        try:
            sync_jc_matches() # 使用 zgzcw 替代 sporttery
            run_quant_collector_job()
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")

        # 2. 审计与自愈 (Audit)
        try:
            daily_audit_job()
        except Exception as e:
            logger.error(f"Audit failed: {e}")

        # 3. 神经网络重训 (MLOps)
        if datetime.now().weekday() == 0 and datetime.now().hour == 4:
            logger.info("🧠 ProQuant Factory: Triggering weekly Stacking NN retrain...")
            trainer = StackingTrainer(db_session=db_session)
            trainer.train()

        logger.info("🏭 ProQuant Factory: Cycle completed.")

factory = QuantFactory()
