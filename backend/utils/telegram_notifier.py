#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_notifier.py - 免费的 Telegram Bot 即时警报消息推送通道

功能：
- 读取环境变量，通过官方 Telegram Bot API 接口向用户的手机/频道推送 Markdown 格式的资金异常与 AI 情报综合报告。
- 内置网络超时防御与发送异常捕获。
"""

import os
import httpx

def send_telegram_markdown_message(text: str) -> bool:
    """
    向配置好的 Telegram Chat ID 推送 Markdown 消息。
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("  [Telegram] ⚠️ 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，跳过 Telegram 推送。")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }
    
    try:
        # 使用同步请求，超时设为 8 秒
        resp = httpx.post(url, json=payload, timeout=8.0)
        if resp.status_code == 200:
            print("  [Telegram] 📡 已成功将异常预警报告推送至您的手机。")
            return True
        else:
            print(f"  [Telegram] ❌ 发送失败，状态码: {resp.status_code}, 详情: {resp.text}")
            return False
    except Exception as e:
        print(f"  [Telegram] ❌ 发送时发生网络异常: {e}")
        return False

if __name__ == "__main__":
    # 临时测试本地连通性
    import sys
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    
    test_msg = "🔥 *必发资金流预警系统测试消息*\n\n24小时资金流与 AI 舆情联动通道已成功建立！"
    send_telegram_markdown_message(test_msg)
