import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.mt5.factory import make_connector


async def main():
    cli, source = make_connector()
    out = {}
    for tf, n in (("M5", 2000), ("M15", 800)):
        candles = await cli.get_candles("XAUUSD", tf, n)
        out[tf] = [
            {"t": c.time, "o": c.open, "h": c.high, "l": c.low,
             "c": c.close, "v": c.tick_volume}
            for c in candles
        ]
        print(f"  {tf}: {len(out[tf])} bars  {out[tf][0]['t']} -> {out[tf][-1]['t']}")
    print("  source:", source)
    try:
        hist = await cli.get_history(days=7)
        print(f"  history deals: {len(hist)}")
        out["_history"] = hist
    except Exception as e:
        print("  history unavailable:", e)

    print("  saved /tmp/candles.json")
    with open("/tmp/candles.json", "w") as f:
        json.dump(out, f)


asyncio.run(main())