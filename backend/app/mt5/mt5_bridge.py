"""In-process MT5 MCP bridge (stdio JSON-RPC 2.0, MCP-compatible).

Exposes ONLY the whitelisted MT5 tools to the trading core. No shell, no
filesystem, no OS access. Requests from the core are validated against
ALLOWED_TOOLS; unknown tools are rejected.

Run:  python -m app.mt5.mcp_server
The trading core spawns this via MT5_MCP_COMMAND (stdio transport).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

from app.mt5.mcp_tools import (
    account_get_history,
    account_get_positions,
    account_get_status,
    market_get_candles,
    market_get_indicator,
    market_get_price,
    market_get_symbol_info,
    system_get_status,
    trade_close,
    trade_modify,
    trade_open,
)

PROTOCOL_VERSION = "2024-11-05"

TOOLS_SPEC = [
    {"name": "market.get_price", "description": "Current bid/ask/spread for a symbol",
     "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "market.get_candles", "description": "OHLC candles (ascending)",
     "inputSchema": {"type": "object",
                     "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string"}, "count": {"type": "integer"}},
                     "required": ["symbol", "timeframe", "count"]}},
    {"name": "market.get_indicator", "description": "Indicator series (EMA/SMA/RSI/ATR)",
     "inputSchema": {"type": "object",
                     "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string"},
                                    "name": {"type": "string"}, "period": {"type": "integer"}},
                     "required": ["symbol", "timeframe", "name", "period"]}},
    {"name": "market.get_symbol_info", "description": "Symbol contract specification",
     "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "account.get_status", "description": "Account balance/equity/mode", "inputSchema": {"type": "object"}},
    {"name": "account.get_positions", "description": "Open positions", "inputSchema": {"type": "object"}},
    {"name": "account.get_history", "description": "Closed deals from recent history",
     "inputSchema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "trade.open", "description": "Open a position (Risk Gate must already have passed upstream)",
     "inputSchema": {"type": "object",
                     "properties": {"symbol": {"type": "string"}, "side": {"type": "string"}, "lot": {"type": "number"},
                                    "sl": {"type": "number"}, "tp": {"type": "number"}, "comment": {"type": "string"}},
                     # sl/tp are OPTIONAL. trade_open() reads them via args.get()
                     # and _opt_float() maps an absent value to None, which MT5
                     # accepts as "no stop level". They were listed as required
                     # here, which misdescribed the tool: the bridge never
                     # enforced this list, so callers were told they could not
                     # omit SL/TP when they could.
                     "required": ["symbol", "side", "lot"]}},
    {"name": "trade.close", "description": "Close a position by ticket",
     "inputSchema": {"type": "object", "properties": {"ticket": {"type": "integer"}}, "required": ["ticket"]}},
    {"name": "trade.modify", "description": "Modify SL/TP of a position",
     "inputSchema": {"type": "object",
                     "properties": {"ticket": {"type": "integer"}, "sl": {"type": "number"}, "tp": {"type": "number"}},
                     "required": ["ticket", "sl", "tp"]}},
    {"name": "system.get_status", "description": "Bridge/terminal status", "inputSchema": {"type": "object"}},
]


def _safe(fn, args: dict) -> dict:
    """Call a tool, never raising across the RPC boundary."""
    try:
        return {"ok": True, **fn(args)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def handle_call(name: str, args: dict) -> dict:
    if name == "market.get_price":
        return _safe(market_get_price, args)
    if name == "market.get_candles":
        return _safe(market_get_candles, args)
    if name == "market.get_indicator":
        return _safe(market_get_indicator, args)
    if name == "market.get_symbol_info":
        return _safe(market_get_symbol_info, args)
    if name == "account.get_status":
        return _safe(account_get_status, args)
    if name == "account.get_positions":
        return _safe(account_get_positions, args)
    if name == "account.get_history":
        return _safe(account_get_history, args)
    if name == "trade.open":
        return _safe(trade_open, args)
    if name == "trade.close":
        return _safe(trade_close, args)
    if name == "trade.modify":
        return _safe(trade_modify, args)
    if name == "system.get_status":
        return _safe(system_get_status, args)
    return {"ok": False, "error": f"tool not allowed: {name}"}


def serve() -> None:  # pragma: no cover - manual run entry
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mt5-bridge", "version": "1.0.0"}}
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS_SPEC}
        elif method == "tools/call":
            params = msg.get("params") or {}
            out = handle_call(str(params.get("name")), dict(params.get("arguments") or {}))
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
        else:
            if rid is None:
                continue
            _emit(rid, None, {"code": -32601, "message": f"method not found: {method}"})
            continue
        if rid is not None:
            _emit(rid, result, None)


def _emit(rid, result, error) -> None:
    resp: dict = {"jsonrpc": "2.0", "id": rid}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    serve()
