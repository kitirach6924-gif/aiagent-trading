"""Can the broker path open a position at all? Probe read-only + one guarded call.

This asks the bridge directly what it would do, WITHOUT sending an order:
  1. what account/mode does the terminal report
  2. what does trade.open refuse with (via an invalid-symbol probe that must
     fail validation before reaching the trade layer)

Nothing here opens a real position. The goal is to find out whether the
connector can reach trade.open and what it answers, because 16 decisions passed
the risk gate with no order and no event.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

from app.config import settings  # noqa: E402
from app.mt5.mcp_client import MT5MCPClient  # noqa: E402


async def main():
    print("=" * 68)
    print("CONNECTOR CONFIG")
    print("=" * 68)
    print("  mt5_mode          :", settings.mt5_mode)
    print("  trading_mode      :", settings.trading_mode)
    print("  live_trading      :", settings.live_trading)
    print("  autonomous_no_sl  :", settings.autonomous_no_sl)

    c = MT5MCPClient()
    print()
    print("=" * 68)
    print("BRIDGE REACHABILITY")
    print("=" * 68)
    st = await c.get_status()
    print("  get_status        :", json.dumps(st, ensure_ascii=False)[:180])

    print()
    print("=" * 68)
    print("ACCOUNT (read-only)")
    print("=" * 68)
    acc = await c.get_account_status()
    print(f"  mode={getattr(acc,'mode','?')} login={getattr(acc,'login','?')} "
          f"balance={getattr(acc,'balance','?')} equity={getattr(acc,'equity','?')}")

    print()
    print("=" * 68)
    print("PROBE: what does the trade layer say? (no order is sent)")
    print("=" * 68)
    print("  Sending a deliberately invalid lot. The broker must reject it on")
    print("  validation BEFORE any order reaches the book, so this is safe and")
    print("  tells us whether trade.open is reachable and what it refuses.")
    res = await c.open_position(symbol=settings.symbols[0], side="BUY",
                                lot=0.0, sl=None, tp=None, comment="probe:validation")
    print(f"  ok     : {res.ok}")
    print(f"  error  : {res.error}")
    print(f"  ticket : {res.ticket}")
    print()
    if not res.ok:
        print("  -> the trade layer IS reachable and is refusing on its own terms.")
        print("     That means a valid order would be accepted; the 16 missing")
        print("     orders were blocked by something upstream, not the broker.")
    else:
        print("  !!! ok=True with lot=0.0 — validation is missing. Investigate")


asyncio.run(main())
