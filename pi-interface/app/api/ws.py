"""
WebSocket connection manager and message broadcaster.

All connected clients receive periodic state broadcasts. On connection,
the client immediately receives the current state so it can synchronise
after a reconnect.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketState

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.debug("WebSocket client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.debug("WebSocket client disconnected (%d remaining)", len(self._clients))

    async def broadcast(self, payload: dict) -> None:
        """Send a JSON payload to all connected clients. Silently drop dead sockets."""
        if not self._clients:
            return
        message = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(message)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def send_console(self, direction: str, line: str) -> None:
        """Push a single console line to all clients."""
        await self.broadcast({
            "type": "console",
            "direction": direction,
            "line": line,
        })

    @property
    def client_count(self) -> int:
        return len(self._clients)
