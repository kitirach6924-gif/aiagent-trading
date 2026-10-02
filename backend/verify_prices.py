"""Sanity check: do the candles match the prices the broker actually filled?

The swing simulation returned 0% winners, which is a strong claim. Before
accepting it, confirm the candles are the same instrument and the same
price scale as the real deals — otherwise the whole comparison is void.
"""
import json
from statistics import median

data = json.load(open("/tmp/candles.json"))
hist = data.get("_history", [])

for TF in ("M5", "M15"):
    b = data.get(TF, [])
    if not b:
        continue
    hs = [x["h"] for x in b]
    ls = [x["l"] for x in b]
    print(f"  {TF}: {len(b)} bars")
    print(f"      high {max(hs):.2f}  low {min(ls):.2f}  "
          f"last close {b[-1]['c']:.2f}")
    print(f"      bar-to-bar |move| median {median([abs(b[i]['c']-b[i-1]['c']) for i in range(1,len(b))]):.2f}")
    print(f"      full range {max(hs)-min(ls):.1f} points")

print()
print("  real deals from the broker:")
for h in hist[:6]:
    print(f"      {h.get('symbol')} {h.get('side')} entry={h.get('entry_price')} "
          f"exit={h.get('exit_price')} pnl={h.get('pnl')}")

syms = {h.get("symbol") for h in hist}
print(f"\n  symbols in history: {syms}")
eps = [h.get("entry_price") for h in hist if h.get("entry_price")]
if eps:
    print(f"  deal entry range: {min(eps):.2f} .. {max(eps):.2f}")

# The decisive comparison: how far did each real deal's price travel?
moves = []
for h in hist:
    e, x = h.get("entry_price"), h.get("exit_price")
    if e and x:
        moves.append(abs(float(x) - float(e)))
if moves:
    print(f"\n  real deal |price move|: median {median(moves):.2f} points, "
          f"max {max(moves):.2f}")
    print(f"  -> deals move {median(moves):.1f} pts vs candles move "
          f"{median([abs(b[i]['c']-b[i-1]['c']) for i in range(1,len(b))]):.1f} pts/bar")

print()
print("  entry==exit in history?:",
      sum(1 for h in hist if h.get("entry_price") == h.get("exit_price")),
      "of", len(hist))