"""Connector factory: bundled MT5 bridge (default), external MCP, or Simulator."""
from __future__ import annotations

from app.config import settings
from app.mt5.mcp_client import MT5MCPClient
from app.mt5.simulator import SimulatorConnector


def make_connector():
    """Return (connector, source) so callers never confuse SIMULATOR with REAL_MT5.

    `source` is one of REAL_MT5 | SIMULATOR and must be reported separately from
    `connected` — a simulator connector that answers calls is NOT a connected MT5.
    """
    if settings.mt5_mode == "MCP":
        if settings.trading_mode == "LIVE" and settings.live_trading:
            # Fail-closed: LIVE mode must talk to real MT5 — never silently simulate.
            client = MT5MCPClient()
            client.ensure_initialized()  # raises if unavailable
            return client, "REAL_MT5"
        try:
            client = MT5MCPClient()
            client.ensure_initialized()
            return client, "REAL_MT5"
        except Exception:
            if settings.trading_mode == "LIVE":
                raise
            # DEMO/dev: fall back to the simulator so the platform stays usable.
            # The caller is told explicitly — see module docstring contract.
            return SimulatorConnector(symbols=settings.symbols), "SIMULATOR"
    return SimulatorConnector(symbols=settings.symbols), "SIMULATOR"
