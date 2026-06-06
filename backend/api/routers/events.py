from fastapi import APIRouter
from starlette.responses import StreamingResponse

router = APIRouter(prefix="/api/events", tags=["System"])

@router.get("")
async def sse_events():
    """SSE 端点：前端连接此端点接收实时推送"""
    from sse import event_generator
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
