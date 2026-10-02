"""Typed event bus so decoupled components can broadcast to WS / Telegram / Audit."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc() -> str:
    return utcnow().isoformat()


@dataclass
class Event:
    action: str
    result: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    user: str = "system"
    agent: str = "core"
    strategy_version: str = ""
    model: str = ""
    ts: str = field(default_factory=iso_utc)


Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Minimal async pub/sub. Audit + Telegram + WebSocket all subscribe here."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, action: str, handler: Handler) -> None:
        self._subs[action].append(handler)

    async def publish(self, event: Event) -> None:
        for handler in list(self._subs.get("*", [])) + list(self._subs.get(event.action, [])):
            try:
                await handler(event)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the loop
                pass


bus = EventBus()
