"""Realtime broadcaster task: pushes MARKET_TICK / ACCOUNT_SNAPSHOT / HEARTBEAT frames."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.api.ws import manager
from app.core.events import Event


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def market_tick_loop(connector, symbols: list[str], interval: float = 1.0) -> None:
    while True:
        try:
            payload = {}
            for s in symbols:
                try:
                    p = await connector.get_price(s)
                    payload[s] = {"bid": p.bid, "ask": p.ask, "spread": p.spread_points, "ts": p.time}
                except Exception:  # noqa: BLE001 - one symbol down must not kill others
                    payload[s] = {"error": "no_tick"}
            if manager.count:
                await manager.broadcast({"type": "MARKET_TICK", "prices": payload, "ts": utc_iso()})
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval)


async def account_snapshot_loop(connector, interval: float = 3.0) -> None:
    while True:
        try:
            if manager.count:
                acc = await connector.get_account_status()
                positions = await connector.get_positions()
                await manager.broadcast({
                    "type": "ACCOUNT_SNAPSHOT", "ts": utc_iso(),
                    "account": acc.__dict__,
                    "positions": [p.__dict__ for p in positions],
                })
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval)


def make_heartbeat_loop(loop_ref, connector, settings_ref):
    async def heartbeat_loop(interval: float = 5.0) -> None:
        while True:
            try:
                if manager.count:
                    l = loop_ref()
                    mt5 = await connector.get_status()
                    await manager.broadcast({
                        "type": "HEARTBEAT", "ts": utc_iso(),
                        "agent": l.status,
                        "mt5": mt5,
                        "mcp": {"mode": settings_ref.mt5_mode, "connected": bool(mt5.get("connected"))},
                        "trading_mode": settings_ref.trading_mode,
                        "live_trading": settings_ref.live_trading,
                        "symbols": settings_ref.symbols,
                        "data_source": "SIMULATOR" if settings_ref.mt5_mode != "MCP" else ("DEMO" if not settings_ref.live_trading else "LIVE"),
                    })
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(interval)
    return heartbeat_loop


async def event_forwarder(event: Event) -> None:
    if manager.count:
        await manager.broadcast({"type": "EVENT", "ts": event.ts, "action": event.action,
                                 "result": event.result, "agent": event.agent,
                                 "strategy_version": event.strategy_version,
                                 "payload": event.payload})
