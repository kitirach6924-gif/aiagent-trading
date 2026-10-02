"""End-to-end proof: send exactly what the loop sends, through the real path.

The loop's failing calls were: side=SELL, lot=0.01, sl=None, tp=None. The
earlier probe proved the bridge now reaches MT5 (retcode 10014) using
lot=0.0/sl=None, but that never exercised a real lot size. This sends the
loop's exact arguments and reports the broker's verdict.

SAFETY: this is a real order request on a DEMO account. It opens at most one
0.01-lot position and closes it immediately on success.
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.config import settings  # noqa: E402
from app.mt5.mcp_client import MT5MCPClient  # noqa: E402

SYMBOL = settings.symbols[0]
LOT, SIDE = 0.01, "SELL"   # identical to the 5 failed attempts


async def main():
    c = MT5MCPClient()

    acc = await c.get_account_status()
    print(f"  account : mode={getattr(acc,'mode','?')} balance={getattr(acc,'balance','?')}")
    if getattr(acc, "mode", "") != "DEMO":
        print("  !!! not DEMO — aborting, refusing to send a live order")
        return

    print()
    print(f"  sending the loop's exact args: {SYMBOL} {SIDE} lot={LOT} sl=None tp=None")
    res = await c.open_position(symbol=SYMBOL, side=SIDE, lot=LOT,
                                sl=None, tp=None, comment="agent:proof")
    print(f"  ok     : {res.ok}")
    print(f"  error  : {res.error}")
    print(f"  ticket : {res.ticket}")
    print(f"  price  : {res.price}")

    if res.ok and res.ticket:
        print()
        print("  >>> ORDER ACCEPTED. The sl=None fix works end to end.")
        pos = await c.get_positions()
        rows = pos if isinstance(pos, list) else getattr(pos, "positions", [])
        print(f"  open positions now: {len(rows)}")
        for p in rows:
            print("    ", p)
        close = await c.close_position(symbol=SYMBOL, ticket=res.ticket)
        print(f"  closed proof position: ok={close.ok} {close.error or ''}")
    else:
        print()
        print("  >>> still refused. The broker's own message is above —")
        print("      that text is now the authoritative answer.")


asyncio.run(main())
