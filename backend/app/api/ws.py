"""Realtime WS broadcaster.

Separates channels so the UI can render ticks without re-rendering everything:
  MARKET_TICK      (1s) — price/bid/ask/spread per symbol
  ACCOUNT_SNAPSHOT (3s) — balance/equity/positions (drives P/L panel)
  HEARTBEAT        (5s) — agent/mt5/mcp status + last beat + data source
  EVENT            (push) — trade/risk/chat/system events (from EventBus)
  CHAT_MESSAGE     (push) — agent chat replies
"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self.active)


manager = ConnectionManager()
