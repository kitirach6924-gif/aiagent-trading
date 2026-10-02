"""Is there ANY realised P&L available yet?

The journal has no closed trades, but MT5 knows the live floating P&L of
the open position and the account's realised balance drift. Both are real
evidence about whether M5 flips are earning or bleeding, even without a
single close.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.mt5.factory import make_connector  # noqa: E402


async def main():
    cli, source = make_connector()
    print(f'  source: {source}')
    try:
        acc = await cli.get_account_status()
        print(f"  balance    : {acc.balance:.2f}")
        print(f"  equity     : {acc.equity:.2f}")
        print()
        print(f"  start balance was 10000.00")
        print(f"  drift so far      : {acc.balance - 10000.0:+.2f}")
        print(f"  equity vs start   : {acc.equity - 10000.0:+.2f}")

        positions = await cli.get_positions()
        print(f"\n  open positions: {len(positions)}")
        for p in positions:
            direction = "profit" if p.pnl > 0 else "LOSS"
            print(f"    {p.symbol} {p.side} lot={p.lot}")
            print(f"      entry={p.entry_price}  now={p.current_price}")
            print(f"      pnl=${p.pnl:+.2f}  ({direction})")
            if p.entry_price and p.current_price:
                move = p.current_price - p.entry_price
                pts = move if p.side == "BUY" else -move
                print(f"      price move: {pts:+.2f} points")
                print(f"      spread cost to flip: ${55.0*0.01*2*p.lot*100:.2f}")
                if abs(p.pnl) > 0:
                    be_needed = (55.0 * 0.01 * 2 * p.lot * 100)
                    print(f"      must beat ${be_needed:.2f} to justify the next flip")
    finally:
        close = getattr(cli, "close", None) or getattr(cli, "disconnect", None)
        if close:
            await close()


asyncio.run(main())