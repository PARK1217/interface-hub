"""In-process WebSocket broadcaster for live monitoring streams."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        msg = json.dumps({"channel": channel, "data": payload}, default=str)
        async with self._lock:
            targets = list(self._clients)
        # send outside the lock so a slow client cannot block fan-out
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


ws_manager = WebSocketManager()