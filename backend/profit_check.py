import json
import sqlite3
from collections import defaultdict

SPREAD_POINTS = 55.0
POINT_VALUE = 0.01
CONTRACT = 100.0
LOT = 0.01
COST = SPREAD_POINTS * POINT_VALUE * 2 * LOT * CONTRACT

c = sqlite3.connect("/app/data/trading.db")
c.row_factory = sqlite3.Row

rows = list(c.execute(
    "SELECT id, ts_open, ts_close, side, lot, pnl, exit_reason, features_json "
    "FROM trades WHERE pnl IS NOT NULL ORDER BY id"))

print(f"  closed trades: {len(rows)}")
if not rows:
    print("  (none yet)")

gross = sum(r["pnl"] for r in rows)
cost = len(rows) * COST
print(f"  gross P&L     : ${gross:+.2f}")
print(f"  flip cost     : ${cost:.2f}  ({len(rows)} x ${COST:.2f})")
print(f"  NET           : ${gross - cost:+.2f}")

if rows:
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]
    print()
    print(f"  wins   : {len(wins)}/{len(rows)} ({100*len(wins)/len(rows):.0f}%)")
    if wins:
        print(f"           avg +${sum(r['pnl'] for r in wins)/len(wins):.2f}")
    if losses:
        print(f"  losses : {len(losses)}  avg ${sum(r['pnl'] for r in losses)/len(losses):+.2f}")

    print()
    print("  per trade (net of cost):")
    for r in rows[-12:]:
        print(f"    #{r['id']:<4} {r['side']:<4} {str(r['ts_close'])[:16]:<16} "
              f"${r['pnl']:+7.2f}  net ${r['pnl']-COST:+7.2f}  {r['exit_reason'] or ''}")

    print()
    by_side = defaultdict(list)
    for r in rows:
        by_side[r["side"]].append(r)
    for side, rs in sorted(by_side.items()):
        g = sum(x["pnl"] for x in rs)
        print(f"  {side:<5} n={len(rs):<3} gross ${g:+8.2f}  net ${g-len(rs)*COST:+8.2f}")

    # how much of gross do costs eat?
    if gross > 0:
        print()
        print(f"  costs consumed {100*cost/gross:.0f}% of gross profit")
    elif gross == 0:
        print()
        print("  gross is exactly zero: costs decide the outcome on their own")

# equity context
eq = list(c.execute("SELECT value_json FROM key_value WHERE key='max_equity_seen'"))
if eq:
    print(f"\n  max equity seen: {json.loads(eq[0][0])}")