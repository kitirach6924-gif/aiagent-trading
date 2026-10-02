"""Authenticated HTTP transport for the MT5 MCP bridge (network-facing counterpart
of the stdio bridge in mt5_bridge.py).

WHY THIS EXISTS
---------------
The bundled bridge (python -m app.mt5.mcp_server) speaks newline-delimited JSON-RPC
2.0 over stdio and is therefore reachable only by the process that spawned it. That
works for the local Windows dev loop but gives the VPS trading container no path at
all. This module exposes the SAME tool surface (handle_call / TOOLS_SPEC — no new
tools, no new semantics) over HTTP so a remote container can drive the same terminal.

SECURITY POSTURE (all fail-closed)
----------------------------------
1. Bearer token required on every request except GET /health. No token -> 401.
2. Bind address is explicit; default is 100.69.186.8 (the Tailscale address) so the
   bridge is never accidentally exposed on 0.0.0.0 / the LAN.
3. Server-side DEMO gate: every mutating tool is refused unless the account's live
   trade_mode reads back as DEMO. A REAL/CONTEST account cannot be traded through
   this bridge even if the caller asks for it.
4. MT5_BRIDGE_ALLOW_TRADE=false strips trade.* from the advertised tool list AND
   rejects the calls. Read-only deployment needs no trade capability at all.
5. Client allow-list (MT5_BRIDGE_ALLOWED_IPS) — optional extra, comma separated.

Nothing here changes trading mode, account settings, or the terminal. It is a
transport, not a policy change.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.mt5.mt5_bridge import TOOLS_SPEC, handle_call
from app.mt5 import mcp_tools
from app.mt5.mt5_runtime import ACCOUNT_MODES

MUTATING_TOOLS = {"trade.open", "trade.close", "trade.modify"}

_BIND_HOST = os.environ.get("MT5_BRIDGE_HTTP_HOST", "100.69.186.8")
_BIND_PORT = int(os.environ.get("MT5_BRIDGE_HTTP_PORT", "8765"))
_TOKEN = os.environ.get("MT5_BRIDGE_AUTH_TOKEN", "")
_ALLOW_TRADE = os.environ.get("MT5_BRIDGE_ALLOW_TRADE", "true").strip().lower() in (
    "1", "true", "yes", "on")
_ALLOWED_IPS = {s.strip() for s in os.environ.get("MT5_BRIDGE_ALLOWED_IPS", "").split(",") if s.strip()}


def _advertised_tools() -> list[dict]:
    if _ALLOW_TRADE:
        return TOOLS_SPEC
    return [t for t in TOOLS_SPEC if t["name"] not in MUTATING_TOOLS]


def _demo_confirmed() -> tuple[bool, str]:
    """Read the terminal's own account mode. Never assume, never default to OK."""
    try:
        if not mcp_tools.mt5.initialize():
            return False, f"MT5 initialize failed: {mcp_tools.mt5.last_error()}"
        acc = mcp_tools.mt5.account_info()
        if acc is None:
            return False, "no account logged in"
        mode = ACCOUNT_MODES.get(acc.trade_mode, "UNKNOWN")
        return (mode == "DEMO"), mode
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


class Handler(BaseHTTPRequestHandler):
    server_version = "mt5-bridge-http/1.0"
    protocol_version = "HTTP/1.1"

    # ---- helpers ----
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ip_allowed(self) -> bool:
        return not _ALLOWED_IPS or self.client_address[0] in _ALLOWED_IPS

    def _authorized(self) -> bool:
        """Bearer-token auth. Every non-Bearer path is denied.

        The previous version had a trailing `if not _ALLOW_TRADE: return False`
        followed by `return False`. Both returns were False, so the guard was
        unreachable and the comment above it advertised a "stdio-compatible
        fallback for headerless clients" that never existed. Denying headerless
        requests is the correct fail-closed behaviour; the misleading contract
        in the comment is the defect, so it is removed rather than preserved.
        """
        if not _TOKEN:
            return False  # fail closed: no configured token = no access
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        import hmac
        return hmac.compare_digest(header[7:].strip(), _TOKEN)

    def log_message(self, fmt: str, *args) -> None:  # quieter, one line per call
        sys.stderr.write("[mt5-bridge-http] " + (fmt % args) + "\n")

    # ---- routes ----
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", ""):
            self._send(200, {"ok": True, "service": "mt5-bridge-http", "version": "1.0.0",
                             "transport": "http", "protocol": "jsonrpc-2.0/mcp",
                             "auth": "bearer-token", "trade_enabled": _ALLOW_TRADE})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("", "/", "/rpc", "/mcp"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._ip_allowed():
            self._send(403, {"ok": False, "error": "client ip not allowed"})
            return
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            msg = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}})
            return

        rid = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mt5-bridge", "version": "1.0.0"}}
        elif method in ("notifications/initialized", "initialized"):
            self._send(202, {"ok": True})
            return
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _advertised_tools()}
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name"))
            args = dict(params.get("arguments") or {})

            if name in MUTATING_TOOLS:
                if not _ALLOW_TRADE:
                    self._send(200, {"jsonrpc": "2.0", "id": rid,
                                     "result": {"content": [{"type": "text", "text": json.dumps(
                                         {"ok": False, "error": "trade.* disabled on this bridge"})}]}})
                    return
                ok, mode = _demo_confirmed()
                if not ok:
                    # fail closed — do NOT forward a trade request of any kind
                    self._send(200, {"jsonrpc": "2.0", "id": rid,
                                     "result": {"content": [{"type": "text", "text": json.dumps(
                                         {"ok": False, "error": f"refused: account mode is {mode}, DEMO required"})}]}})
                    return

            out = handle_call(name, args)
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
        else:
            self._send(200, {"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32601, "message": f"method not found: {method}"}})
            return

        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result})


def serve() -> None:  # pragma: no cover - manual run entry
    srv = ThreadingHTTPServer((_BIND_HOST, _BIND_PORT), Handler)
    sys.stderr.write(
        f"[mt5-bridge-http] listening on {_BIND_HOST}:{_BIND_PORT} "
        f"auth={'token' if _TOKEN else 'DISABLED-FAILCLOSED'} trade={_ALLOW_TRADE}\n")
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":  # pragma: no cover
    serve()
