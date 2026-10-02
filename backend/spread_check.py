import asyncio
import sys

sys.path.insert(0, "/app")

from app.mt5.factory import make_connector


async def main():
    cli, source = make_connector()
    info = await cli.get_symbol_info("XAUUSD")
    print("  source:", source)
    print(f"  symbol        : {getattr(info, 'symbol', '?')}")
    for attr in ("spread", "spread_points", "bid", "ask", "digits",
                 "point", "trade_tick_size", "volume_min"):
        if hasattr(info, attr):
            print(f"  {attr:<14}: {getattr(info, attr)}")
    print(f"  all attrs     : {[a for a in dir(info) if not a.startswith('_')]}")
    bid = await cli.get_price("XAUUSD")
    print(f"  price         : {bid}")
    # the decisive number: what does one flip actually cost, in points?
    try:
        print(f"  bid/ask raw   : {bid.bid} / {bid.ask}"
              if hasattr(bid, "bid") else f"  price raw     : {bid}")
    except Exception as e:
        print("  price detail unavailable:", e)


asyncio.run(main())