"""
Server-Sent Events (SSE) 实时推送模块
调度器任务调用 push_event() 推送更新，前端通过 /api/events 接收。
"""
import asyncio
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("sse")

# 所有活跃的 SSE 客户端队列
_clients: list[asyncio.Queue] = []


async def push_event(event: str, data: Dict[str, Any]):
    """向所有连接的客户端推送事件"""
    payload = json.dumps(data, ensure_ascii=False)
    message = f"event: {event}\ndata: {payload}\n\n"
    dead = []
    for q in _clients:
        try:
            await q.put(message)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _clients.remove(q)
        except ValueError:
            pass
    logger.debug(f"[sse] pushed event={event} to {len(_clients)} clients")


async def event_generator():
    """SSE 生成器，每个客户端一个队列"""
    queue: asyncio.Queue = asyncio.Queue()
    _clients.append(queue)
    try:
        # 发送初始连接确认
        yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30)
                yield message
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
    finally:
        try:
            _clients.remove(queue)
        except ValueError:
            pass
