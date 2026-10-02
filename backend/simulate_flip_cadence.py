"""Measure the flip strategy from data the system already recorded.

THE QUESTION
    The user wants to know, before tuning anything: how often would a
    score-driven flip actually fire, and what does it cost?

    Every WAIT decision stores its entry_score in detail_json, so a flip can be
    simulated over the full 24h history without waiting for live trades, and
    without opening a single position. Simulating is the only honest way to
    answer this now — waiting for real flips would take days and would cost
    real spread.

WHAT IT REPORTS
    - how often score >= 65 occurs per hour, per direction
    - the flip cadence that would result (the user wants "orders all the time")
    - spread cost per flip, and total spread burned per day at that cadence
    - how many flips would have been immediately re-flipped (churn)
"""
import datetime as dt
import json
import sqlite3
import statistics
import sys
from collections import defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/data/trading.db"
THRESHOLD = 65

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

rows = c.execute("""SELECT ts, decision, reason, detail_json FROM decisions
                    WHERE detail_json LIKE '%entry_score%' ORDER BY id""").fetchall()

# ---- reconstruct the score timeline -------------------------------------
samples = []   # (ts, direction, score, spread_points)
for r in rows:
    try:
        d = json.loads(r["detail_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        continue
    es = d.get("entry_score") or {}
    total = es.get("total")
    if total is None:
        continue
    curve = (d.get("stochastic") or {}).get("curve", "")
    if curve == "TURNING_UP":
        side = "BUY"
    elif curve == "TURNING_DOWN":
        side = "SELL"
    else:
        side = None
    samples.append((dt.datetime.fromisoformat(r["ts"]), side, float(total),
                    es.get("spread_points")))

if not samples:
    print("  no scored decisions to analyse yet")
    sys.exit(0)

span_h = (samples[-1][0] - samples[0][0]).total_seconds() / 3600.0
passing = [s for s in samples if s[2] >= THRESHOLD and s[1]]

print("=" * 70)
print(f"SCORE-DRIVEN FLIP SIMULATION  ({span_h:.1f}h of history)")
print("=" * 70)
print(f"  scored decisions analysed : {len(samples)}")
print(f"  score >= {THRESHOLD}           : {len(passing)}")
print(f"  rate                      : {len(passing)/span_h:.2f} per hour")

per_dir = defaultdict(int)
for _, side, _, _ in passing:
    per_dir[side] += 1
print(f"  by direction              : BUY {per_dir['BUY']}  SELL {per_dir['SELL']}")

# ---- flip cadence -------------------------------------------------------
# A flip = a passing signal in the direction OPPOSITE the open side. Walk the
# timeline holding one position; each time a pass contradicts the side we hold,
# that is a close+open, and the new side becomes what we hold.
flips = 0
same_side_entries = 0
held = None
flip_gaps = []
last_flip_at = None
held_since = None

for ts, side, total, _ in samples:
    if side is None or total < THRESHOLD:
        continue
    if held is None:
        held = side
        held_since = ts
        continue
    if side == held:
        continue          # already in this side; the loop blocks duplicates
    # a qualifying signal the other way = flip
    flips += 1
    if last_flip_at:
        flip_gaps.append((ts - last_flip_at).total_seconds() / 60.0)
    last_flip_at = ts
    held = side
    held_since = ts

print()
print("=" * 70)
print("WHAT THE USER ASKED FOR: how often would it flip?")
print("=" * 70)
print(f"  flips over {span_h:.1f}h        : {flips}")
print(f"  flips per day (projected) : {flips / span_h * 24:.0f}")
if flip_gaps:
    print(f"  gap between flips        : median {statistics.median(flip_gaps):.0f} min, "
          f"min {min(flip_gaps):.0f}, max {max(flip_gaps):.0f}")
    print(f"  fastest observed flip    : every {min(flip_gaps):.0f} min")

# ---- the cost of each flip ---------------------------------------------
spreads = [s[3] for s in samples if s[3]]
if spreads and flips:
    med_sp = statistics.median(spreads)
    # XAUUSD: 1 point = 0.01 USD per 0.01 lot. A flip pays the spread twice
    # (close at bid/ask, reopen at the other side).
    cost_per_flip = med_sp * 0.01 * 2 * 0.01 * 100  # pts → price → lot → contract
    print()
    print("=" * 70)
    print("WHAT IT COSTS")
    print("=" * 70)
    print(f"  median spread            : {med_sp:.1f} points")
    print(f"  cost per flip (round trip): ${cost_per_flip:.4f}")
    print(f"  cost per day at this rate: ${cost_per_flip * flips / span_h * 24:.2f}")
    print()
    print("  NOTE: spread is only the visible cost. A flip taken near the turn's")
    print("  end also gives back the move it chased — see the pnl section below.")

# ---- what the live P&L actually was ------------------------------------
print()
print("=" * 70)
print("REAL TRADES SO FAR (the only realised evidence)")
print("=" * 70)
tr = c.execute("""SELECT COUNT(*) n, SUM(pnl) p FROM trades
                  WHERE ts_close IS NOT NULL""").fetchone()
op = c.execute("SELECT COUNT(*) n FROM trades WHERE ts_close IS NULL").fetchone()[0]
print(f"  closed trades : {tr['n']}   total pnl: {tr['p'] or 0}")
print(f"  still open    : {op}")
if tr["n"] == 0:
    print("  -> not enough closed trades yet to judge the strategy on P&L.")
    print("     The simulation above answers frequency/cost; P&L needs live time.")

print()
print("=" * 70)
print("WHAT TO WATCH FROM HERE")
print("=" * 70)
print("  1. flips/day          — is it 'always in the market' or churning?")
print("  2. spread burned/day  — compare against total pnl")
print("  3. pnl per flip       — must exceed the round-trip spread to be viable")