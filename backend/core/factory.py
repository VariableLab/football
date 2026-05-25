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
from sporttery_sync import sporttery_daily_sync_job
from jingcai_quant_collector import run_quant_collector_job
from monitor.model_audit import daily_audit_job
from residual_nn import residual_nn_train_job

logger = logging.getLogger("quant_factory")

class QuantFactory:
    def __init__(self):
        self.is_running = False

    async def run_cycle(self):
        """主生产循环"""
        logger.info("🏭 ProQuant Factory: Starting production cycle...")
        
        # 1. 同步数据 (Ingestion)
        try:
            sporttery_daily_sync_job()
            run_quant_collector_job()
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")

        # 2. 预测生成 (已在 sync 任务中包含自动触发)
        
        # 3. 审计与自愈 (Audit)
        try:
            daily_audit_job()
        except Exception as e:
            logger.error(f"Audit failed: {e}")

        # 4. 神经网络重训 (MLOps) - 每周触发一次
        if datetime.now().weekday() == 0 and datetime.now().hour == 4:
            logger.info("🧠 ProQuant Factory: Triggering weekly NN retrain...")
            residual_nn_train_job()

        logger.info("🏭 ProQuant Factory: Cycle completed.")

factory = QuantFactory()
