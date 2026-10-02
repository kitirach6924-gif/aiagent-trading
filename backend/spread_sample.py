import asyncio
import sys
from statistics import median, mean

sys.path.insert(0, "/app")

from app.mt5.factory import make_connector


async def main():
    cli, source = make_connector()
    print(f"  source: {source}")
    samples = []
    print("\n  sampling the live quote 12x over ~40 seconds:")
    for i in range(12):
        p = await cli.get_price("XAUUSD")
        sp = (p.ask - p.bid) / 0.01
        samples.append(sp)
        print(f"    {i+1:>2}  bid {p.bid:.2f}  ask {p.ask:.2f}  "
              f"spread {sp:.0f} pts = ${sp*0.01*0.01*100:.2f}")
        await asyncio.sleep(3)

    print(f"\n  spread points: median {median(samples):.0f}, "
          f"min {min(samples):.0f}, max {max(samples):.0f}")
    print(f"  in dollars (0.01 lot): median ${median(samples)*0.01:.2f} "
          f"per round trip")

    # What a typical XAUUSD spread should look like, for comparison
    print("\n  reference: XAUUSD spreads in the wild")
    print("    typical retail : 20-40 points ($0.20-$0.40)")
    print("    good ECN/ECM   :  5-20 points ($0.05-$0.20)")
    print(f"    THIS account  : {median(samples):.0f} points "
          f"(${median(samples)*0.01:.2f})")


asyncio.run(main())