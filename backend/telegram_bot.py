import asyncio
import httpx
import os
import sys
from pathlib import Path

# 设置路径以引入 backend 模块
_current_dir = Path(__file__).resolve().parent
sys.path.append(str(_current_dir))

from database.config import get_settings
from database.models import SessionLocal
from utils.logger import get_logger

logger = get_logger("telegram_bot")
settings = get_settings()

class TelegramAgent:
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        if not self.token or not self.chat_id:
            logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
            sys.exit(1)
            
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0

    async def get_updates(self):
        """长轮询获取新消息"""
        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(
                    f"{self.api_url}/getUpdates",
                    params={"offset": self.offset, "timeout": 30},
                    timeout=35
                )
                if res.status_code == 200:
                    return res.json().get("result", [])
            except Exception as e:
                logger.error(f"Polling error: {e}")
        return []

    async def send_message(self, text: str):
        """发送 Markdown 格式消息"""
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{self.api_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
                )
            except Exception as e:
                logger.error(f"Send failed: {e}")

    async def process_message(self, text: str):
        """自然语言意图分发中心"""
        text = text.lower()
        
        # ─── 意图 1：修复 / 重训 ───
        if "修复" in text or "重训" in text or "train" in text:
            await self.send_message("⚙️ 收到指令：正在执行模型全量修复 (Stacking NN)...")
            from core.residual_nn import StackingTrainer, StackingNet
            db = SessionLocal()
            try:
                trainer = StackingTrainer(db_session=db)
                # 确保维度对齐
                trainer.model = StackingNet(input_dim=59)
                result = trainer.train()
                await self.send_message(
                    f"✅ **模型修复完成**\n\n"
                    f"新权重特征:\n"
                    f"- 最优 Loss: {result.get('best_loss', 0):.4f}\n"
                    f"- 样本量校验已通过。"
                )
            except Exception as e:
                await self.send_message(f"❌ **修复失败**: `{e}`")
            finally:
                db.close()
                
        # ─── 意图 2：验证 / 战报 ───
        elif "验证" in text or "结果" in text or "validate" in text:
            await self.send_message("📊 正在运行验证引擎核查最近 100 场...")
            from monitor.validation_engine import ValidationEngine
            db = SessionLocal()
            try:
                report = ValidationEngine.run_validation(db, limit=100)
                msg = (
                    f"✅ **实时验证结果**\n"
                    f"- 样本量: {report.validated_matches} 场\n"
                    f"- 方向准确率: **{report.direction_accuracy:.2%}**\n"
                    f"- Brier Score: {report.avg_brier_score:.4f}\n\n"
                    f"状态评估: {'🚨 模型衰退 (建议输入“修复”)' if report.direction_accuracy < 0.48 else '✅ 状态良好'}"
                )
                await self.send_message(msg)
            except Exception as e:
                await self.send_message(f"❌ **验证失败**: `{e}`")
            finally:
                db.close()
                
        # ─── 意图 3：扫描机会 ───
        elif "扫描" in text or "机会" in text or "scan" in text:
            await self.send_message("🔍 正在扫描全站极端错价与焦点战...")
            from core.agent_tools import AgentTools
            db = SessionLocal()
            try:
                anomalies = AgentTools.scan_market_anomalies(db)
                if anomalies:
                    msg = "🚨 **发现高价值错价 (Edge > 12%)**\n\n"
                    for a in anomalies:
                        msg += f"• `{a['match']}`\n  类型: {a['type']}\n  价值: **{a['value']}**\n\n"
                else:
                    msg = "ℹ️ 当前未发现大 Edge 错价机会，市场定价有效。"
                await self.send_message(msg)
            except Exception as e:
                await self.send_message(f"❌ **扫描失败**: `{e}`")
            finally:
                db.close()
                
        # ─── Fallback：无效指令 ───
        else:
            await self.send_message(
                "🤖 **WC Analytics Agent**\n\n"
                "我未能识别此指令。你可以发送：\n"
                "- `修复` / `重训`：强制对齐特征并重练神经网络。\n"
                "- `验证` / `结果`：跑批验证模型的实时准确率。\n"
                "- `扫描` / `机会`：寻找市场中的错价 (Extreme Edge)。"
            )

    async def run(self):
        logger.info("🤖 Telegram Agent is polling...")
        await self.send_message("🟢 **WC Analytics Agent Online**\n你可以随时发送自然语言指令控制我。")
        while True:
            updates = await self.get_updates()
            for update in updates:
                self.offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg: continue
                
                chat_id = str(msg.get("chat", {}).get("id"))
                if chat_id != str(self.chat_id):
                    logger.warning(f"阻断了来自未授权账户的访问: {chat_id}")
                    continue
                    
                text = msg.get("text", "")
                if text:
                    logger.info(f"Received Command from {chat_id}: {text}")
                    # 在后台运行，不阻塞轮询
                    asyncio.create_task(self.process_message(text))
                    
            await asyncio.sleep(1)

if __name__ == "__main__":
    agent = TelegramAgent()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("\nAgent stopped.")
