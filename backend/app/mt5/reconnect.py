"""Keeps the MT5 connector pointed at the real bridge once it appears.

The connector is built once at import time. If the Windows bridge is not up then —
which is the normal case after a reboot, since it is a separate Windows process —
`make_connector()` falls back to the simulator and the whole app runs on fake data
for the rest of its life. That is the worst possible failure for a trading system:
it looks healthy and trades, just not the real market.

This module retries the real connection in the background and swaps it in as soon
as it succeeds, so a late-starting bridge is picked up without a container restart.
Fail-closed rules are preserved: LIVE mode never silently gets a simulator.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from app.core.events import Event, bus
from app.mt5.factory import make_connector

log = logging.getLogger("mt5.reconnect")

# Consecutive failed probes before alerting (× interval seconds). Two at 20s ≈ 40s:
# long enough to ignore a blip, short enough that a dead bridge is noticed fast.
DOWN_ALERT_AFTER = 2


class ConnectorSupervisor:
    """Holds the live connector and re-attempts REAL_MT5 until it is available."""

    def __init__(self, initial, source: str) -> None:
        self._connector = initial
        self._source = source
        self._tasks: list[asyncio.Task] = []

    @property
    def connector(self):
        return self._connector

    @property
    def source(self) -> str:
        return self._source

    def snapshot(self) -> tuple:
        return self._connector, self._source

    async def watch(self, on_change=None, interval: float = 20.0) -> None:
        """Poll for the real bridge and upgrade the connector when it appears.

        Also watches the other direction: a bridge that was up and has gone away is
        the case that needs the user, because the agent goes quiet and they would
        otherwise have no idea trading has silently stopped.
        """
        if settings.mt5_mode != "MCP":
            return
        misses = 0
        while True:
            try:
                if self._source != "REAL_MT5":
                    client, src = make_connector()
                    if src == "REAL_MT5":
                        log.warning("MT5 bridge reachable — upgrading from %s to REAL_MT5",
                                    self._source)
                        was_down = misses >= DOWN_ALERT_AFTER
                        self._connector, self._source = client, src
                        misses = 0
                        if was_down:
                            await self._notify_up()
                        if on_change:
                            r = on_change(client, src)
                            if asyncio.iscoroutine(r):
                                await r
                    else:
                        # LIVE must never run on a simulator: surface it instead of retrying.
                        if settings.trading_mode == "LIVE":
                            log.error("LIVE mode with no MT5 bridge — refusing to stay on SIMULATOR")
                            return
                        misses += 1
                        if misses == DOWN_ALERT_AFTER:
                            await self._notify_down(reason="no bridge answering")
                        elif misses == DOWN_ALERT_AFTER * 6:
                            await self._notify_down(reason="bridge still down after extended retries")
                else:
                    # Connected: confirm the link is still alive, not just assumed.
                    if await self._alive():
                        misses = 0
                    else:
                        misses += 1
                        log.warning("MT5 bridge health check failed (%s consecutive)", misses)
                        if misses == DOWN_ALERT_AFTER:
                            await self._notify_down(reason="bridge stopped responding")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("MT5 reconnect probe failed: %s", e)
                misses += 1
                if misses == DOWN_ALERT_AFTER:
                    await self._notify_down(reason=f"probe error: {e}"[:120])
                await asyncio.sleep(interval)

    async def _alive(self) -> bool:
        """Cheap liveness probe: a real round-trip, never an assumed connection."""
        try:
            await asyncio.wait_for(self._connector.get_status(), timeout=8.0)
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("MT5 liveness probe failed: %s", e)
            return False

    async def _notify_down(self, reason: str) -> None:
        log.error("MT5 bridge DOWN: %s", reason)
        await bus.publish(Event(
            action="MT5_BRIDGE_DOWN", agent="mt5_supervisor",
            result=(f"MT5 bridge ไม่ตอบสนอง ({reason}) — ระบบเทรดอาจหยุดชั่วคราว "
                    f"หรือกำลังใช้ข้อมูลจำลอง โปรดเช็ค MT5/bridge บนเครื่อง Windows"),
            payload={"reason": reason, "source": self._source}))

    async def _notify_up(self) -> None:
        await bus.publish(Event(
            action="MT5_BRIDGE_UP", agent="mt5_supervisor",
            result="MT5 bridge กลับมาเชื่อมต่อแล้ว — กลับมาเทรดกับบัญชีจริง",
            payload={"source": self._source}))

    def source_now(self) -> str:
        """Read the live source from the supervisor, not a stale startup snapshot."""
        return self._source

    def connector_now(self):
        return self._connector

    def mark(self, connector, source: str) -> None:
        self._connector, self._source = connector, source

    def start(self, on_change=None, interval: float = 20.0) -> None:
        if not self._tasks:
            self._tasks.append(asyncio.create_task(self.watch(on_change, interval)))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []


def wait_for_bridge(timeout: float = 20.0, interval: float = 2.0) -> bool:
    """Blocking helper for startup scripts: is the real bridge answering yet?"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _, src = make_connector()
            if src == "REAL_MT5":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False
