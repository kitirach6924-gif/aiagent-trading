"""MCP-based MT5 connector.

Only whitelisted MT5 MCP tools are callable. There is deliberately NO shell /
filesystem / arbitrary-exec tool here: the agent can never reach the OS.

Transports (in priority order):
  1. External command (stdio)      — MT5_MCP_COMMAND set
  2. External HTTP server          — MT5_BRIDGE_INPROCESS=false, host/port reachable
  3. Bundled in-process stdio bridge (`python -m app.mt5.mcp_server`) — default,
     attaches to the locally running MT5 terminal via the MetaTrader5 package.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request

from app.config import settings
from app.mt5 import providers
from app.mt5.base import (
    AccountStatus,
    Candle,
    Position,
    Price,
    SymbolInfo,
    TradeResult,
)

ALLOWED_TOOLS = {
    "market.get_price", "market.get_candles", "market.get_indicator", "market.get_symbol_info",
    "account.get_status", "account.get_positions", "account.get_history",
    "trade.open", "trade.close", "trade.modify",
    "system.get_status",
}


class MT5MCPClient:
    """Thin JSON-RPC MCP client over stdio or HTTP.

    Symbol mapping: รับ canonical (XAUUSD) → ยิง tool ด้วย broker symbol ของ
    provider ปัจจุบัน (เช่น XM=GOLD) และ map กลับเป็น canonical ในผลลัพธ์ —
    ทำให้ strategy/config อิง XAUUSD ตัวเดียวได้กับทุกโบรกเกอร์
    """

    def __init__(self, command: str | None = None, http_base: str | None = None) -> None:
        self.command = command or settings.mt5_mcp_command
        self.http_base = http_base or f"http://{settings.mt5_server_host}:{settings.mt5_server_port}"
        self._proc = None
        self._req_id = 0
        self._initialized = False

    # ---- provider symbol mapping ----
    @staticmethod
    def _to_broker(symbol: str) -> str:
        return providers.symbol_for(symbol)

    @staticmethod
    def _to_canonical(symbol: str) -> str:
        rev = {v: k for k, v in providers.get_active_provider().symbol_map.items()}
        return rev.get(symbol, symbol)

    # ---------- transport ----------
    def _start(self):
        if self._proc is None:
            import subprocess

            if self.command:
                self._proc = subprocess.Popen(
                    self.command, shell=True,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
            else:
                # bundled in-process bridge: same python, same app package
                self._proc = subprocess.Popen(
                    [sys.executable, "-m", "app.mt5.mcp_server"],
                    cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
        return self._proc

    def _use_http(self) -> bool:
        return not self.command and not settings.mt5_bridge_inprocess

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}}
        if timeout is None:
            timeout = float(getattr(settings, "mt5_bridge_timeout_seconds", 15.0) or 15.0)
        if self._use_http():
            return self._rpc_http(payload, timeout)
        return self._rpc_stdio(payload, timeout)

    def _rpc_http(self, payload: dict, timeout: float) -> dict:
        url = self.http_base.rstrip("/") + "/"
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        token = getattr(settings, "mt5_bridge_auth_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            if raw.startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        break
            msg = json.loads(raw)
            # The HTTP bridge returns a full JSON-RPC envelope; the stdio path returns
            # only the inner result. Normalise here so both transports agree, otherwise
            # every HTTP tool call surfaces {"jsonrpc":...,"result":{...}} and the
            # payload keys (bid/ask/candles/...) are missing.
            if isinstance(msg, dict) and "result" in msg and "jsonrpc" in msg:
                if msg.get("error"):
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg["result"]
            return msg

    def _rpc_stdio(self, payload: dict, timeout: float) -> dict:
        for attempt in (1, 2):
            try:
                # A hung bridge used to block this call forever (the timeout arg was
                # accepted and ignored). Readline now runs under a real deadline.
                return self._readline_with_deadline(payload, timeout)
            except (OSError, ConnectionError, AssertionError, TimeoutError) as e:
                # bridge died, hung, or pipe broke — respawn once and retry (self-healing)
                if attempt == 2:
                    raise
                try:
                    if self._proc and self._proc.poll() is None:
                        self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                self._proc = None
                self._initialized = False

    def _readline_with_deadline(self, payload: dict, timeout: float) -> dict:
        proc = self._proc
        assert proc is not None and proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        box: list[dict] = []

        def _reader() -> None:
            while True:
                line = proc.stdout.readline()
                if not line:
                    box.append({"__closed__": True})
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == payload["id"]:
                    box.append(msg)
                    return

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout)
        if not box:
            raise TimeoutError(f"MCP bridge did not answer within {timeout}s")
        msg = box[0]
        if msg.get("__closed__"):
            raise ConnectionError("MCP server closed the stream")
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return msg.get("result", {})

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._start()
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "trading-core"}})
        self._initialized = True

    # ---------- tool invocation ----------
    def call_tool(self, name: str, args: dict | None = None) -> dict:
        if name not in ALLOWED_TOOLS:
            raise PermissionError(f"tool not allowed: {name}")
        self.ensure_initialized()
        result = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        if isinstance(result, dict) and "content" in result:
            texts = [c.get("text", "") for c in result["content"] if c.get("type") == "text"]
            raw = "\n".join(texts)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw}
        return result

    # ---------- connector API ----------
    async def get_price(self, symbol: str) -> Price:
        d = self.call_tool("market.get_price", {"symbol": self._to_broker(symbol)})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        return Price(symbol=symbol, bid=float(d["bid"]), ask=float(d["ask"]),
                     spread_points=float(d.get("spread_points", 0.0)),
                     time=str(d.get("time", "")))

    async def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        d = self.call_tool("market.get_candles", {"symbol": self._to_broker(symbol),
                                                  "timeframe": timeframe, "count": count})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        rows = d.get("candles", d if isinstance(d, list) else [])
        return [Candle(time=str(r["time"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]),
                       tick_volume=float(r.get("tick_volume", 0))) for r in rows]

    async def get_indicator(self, symbol: str, timeframe: str, name: str, period: int) -> list[float]:
        d = self.call_tool("market.get_indicator", {"symbol": self._to_broker(symbol),
                                                    "timeframe": timeframe,
                                                    "name": name, "period": period})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        vals = d.get("values", d if isinstance(d, list) else [])
        return [float(v) for v in vals]

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        d = self.call_tool("market.get_symbol_info", {"symbol": self._to_broker(symbol)})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        return SymbolInfo(symbol=symbol, digits=int(d.get("digits", 5)), point=float(d.get("point", 0.0)),
                          contract_size=float(d.get("contract_size", 100_000)),
                          min_lot=float(d.get("min_lot", 0.01)), lot_step=float(d.get("lot_step", 0.01)),
                          description=str(d.get("description", "")))

    async def get_account_status(self) -> AccountStatus:
        d = self.call_tool("account.get_status", {})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        return AccountStatus(login=str(d.get("login", "")), name=str(d.get("name", "")),
                             currency=str(d.get("currency", "USD")), balance=float(d.get("balance", 0)),
                             equity=float(d.get("equity", 0)), margin_used=float(d.get("margin_used", 0)),
                             leverage=int(d.get("leverage", 0)), server=str(d.get("server", "")),
                             mode=str(d.get("mode", "DEMO")))

    async def get_positions(self) -> list[Position]:
        d = self.call_tool("account.get_positions", {})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        rows = d.get("positions", d if isinstance(d, list) else [])
        return [Position(ticket=str(r["ticket"]), symbol=self._to_canonical(str(r["symbol"])),
                         side=str(r["side"]),
                         lot=float(r["lot"]), entry_price=float(r["entry_price"]),
                         current_price=float(r.get("current_price", r["entry_price"])),
                         pnl=float(r.get("pnl", 0)), sl=float(r.get("sl", 0)), tp=float(r.get("tp", 0)),
                         opened_at=str(r.get("opened_at", "")),
                         strategy_version=str(r.get("strategy_version", ""))) for r in rows]

    async def get_history(self, days: int = 7) -> list[dict]:
        d = self.call_tool("account.get_history", {"days": days})
        if not d.get("ok", True):
            raise ConnectionError(f"MT5 bridge: {d.get('error')}")
        rows = d.get("history", d if isinstance(d, list) else [])
        return [dict(r) for r in rows]

    async def open_position(self, symbol: str, side: str, lot: float, sl: float, tp: float,
                            comment: str = "") -> TradeResult:
        d = self.call_tool("trade.open", {"symbol": self._to_broker(symbol), "side": side, "lot": lot,
                                          "sl": sl, "tp": tp, "comment": comment})
        return TradeResult(ok=bool(d.get("ok", False)), ticket=str(d.get("ticket", "")),
                           price=float(d.get("price", 0)), error=str(d.get("error", "")))

    async def close_position(self, ticket: str) -> TradeResult:
        d = self.call_tool("trade.close", {"ticket": int(ticket)})
        return TradeResult(ok=bool(d.get("ok", False)), ticket=ticket,
                           price=float(d.get("price", 0)), error=str(d.get("error", "")))

    async def modify_position(self, ticket: str, sl: float, tp: float) -> TradeResult:
        d = self.call_tool("trade.modify", {"ticket": int(ticket), "sl": sl, "tp": tp})
        return TradeResult(ok=bool(d.get("ok", False)), ticket=ticket,
                           error=str(d.get("error", "")))

    async def get_status(self) -> dict:
        try:
            d = self.call_tool("system.get_status", {})
            if not d.get("ok", True):
                return {"connected": False, "mode": "MCP", "error": d.get("error")}
            return {"connected": bool(d.get("connected", True)), "mode": d.get("mode", "MCP"),
                    "terminal": d.get("terminal", {})}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "mode": "MCP", "error": str(e)}
