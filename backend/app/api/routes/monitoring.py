from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.websocket import ws_manager

router = APIRouter(tags=["monitoring"])


@router.websocket("/ws/monitoring")
async def monitoring_socket(websocket: WebSocket) -> None:
    """Live channel for call_logs / incidents / stats events.

    Frontend listens here; backend services call `ws_manager.broadcast(channel, payload)`.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # we don't expect inbound messages, but read keeps the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket)
