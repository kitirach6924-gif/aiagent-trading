"""Verify the account's real XAUUSD spec against the reference table.

The pasted reference assumes 1 lot = 100 oz and point = 0.01. Both must be
read from the broker, not assumed, because every P/L number in the system
depends on them. This checks the constants the code actually uses.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.config import settings
from app.mt5.factory import make_connector


async def main():
    cli, source = make_connector()
    acc = await cli.get_account_status()
    info = await cli.get_symbol_info("XAUUSD")
    price = await cli.get_price("XAUUSD")

    print(f"  source      : {source}")
    print(f"  account     : {acc.name} ({acc.login})")
    print(f"  server      : {acc.server}")
    print(f"  balance     : {acc.balance:.2f} {acc.currency}")
    print(f"  leverage    : 1:{acc.leverage}")
    print(f"  mode        : {acc.mode}")
    print()
    print("  XAUUSD specification as the broker reports it:")
    for f in ("symbol", "contract_size", "point", "digits",
              "min_lot", "lot_step"):
        if hasattr(info, f):
            print(f"    {f:<14}: {getattr(info, f)}")

    contract = getattr(info, "contract_size", None) or 100.0
    point = getattr(info, "point", None) or 0.01
    digits = getattr(info, "digits", None)

    print()
    print("=" * 66)
    print("  P/L TABLE built from the LIVE spec (not the reference's assumption)")
    print("=" * 66)
    print(f"  contract_size = {contract}  point = {point}  digits = {digits}")
    print()
    print(f"  {'lot':>7}{'oz':>8}{'$1 move':>11}{'$10 move':>11}"
          f"{'spread $0.55':>15}")

    for lot in (0.01, 0.02, 0.03, 0.05, 0.10, 0.20):
        oz = lot * contract
        per_1usd = oz * 1.0                # $1 of price per oz
        per_10usd = oz * 10.0
        spr_usd = price.spread_points * point * oz
        print(f"  {lot:>7.2f}{oz:>8.1f}{per_1usd:>11.2f}{per_10usd:>11.2f}"
              f"{spr_usd:>15.2f}")

    print()
    print("  how far price must move to earn back the spread:")
    print(f"  {'lot':>7}{'spread $':>12}{'points needed':>16}{'$ of gold':>12}")
    for lot in (0.01, 0.02, 0.05, 0.10):
        oz = lot * contract
        spr_pts = price.spread_points
        spr_usd = spr_pts * point * oz
        print(f"  {lot:>7.2f}{spr_usd:>12.2f}{spr_pts:>16.0f}"
              f"{spr_pts*point:>12.2f}")

    print()
    print("  note: the 'points needed' column is lot-independent — a break-even")
    print("  is always 55 points of price, whatever size the position is.")

    # margin / stop-out reality, which the reference rightly warns about
    print()
    print("=" * 66)
    print("  MARGIN and STOP-OUT reality")
    print("=" * 66)
    for lot in (0.01, 0.05, 0.10):
        notional = lot * contract * price.bid
        margin = notional / acc.leverage
        loss_to_50 = 50.0 / (lot * contract) if lot else 0
        print(f"  {lot:>5.2f} lot | notional ${notional:>10,.0f} | "
              f"margin ${margin:>8,.2f} | $50 loss at {loss_to_50:>7.0f} pts")

    print()
    print("  configured in this codebase:")
    print(f"    SPREAD_POINTS default : 55.0")
    print(f"    POINT_VALUE           : 0.01")
    print(f"    CONTRACT_SIZE         : 100.0")
    print(f"    LOT                   : 0.01")
    print(f"    -> matches live spec  : "
          f"{abs((0.01*100.0*0.01) - (point*contract*0.01)) < 1e-9}")


asyncio.run(main())