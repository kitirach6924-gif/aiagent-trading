"""Corrected flip measurement: count distinct TURN EVENTS, not cycles.

WHY THIS REVISION
    The first pass counted every cycle whose score >= 65. But a single
    stochastic turn holds the curve in TURNING_UP for several consecutive
    cycles, each one re-scoring 65. Counting those as separate signals
    overstated the flip rate by an order of magnitude.

    The loop itself is protected against this by ENTRY_COOLDOWN_SECONDS and by
    the duplicate check, so the honest question is: how many DISTINCT turns
    occur, and how many of those are reversals of the side currently held.
"""
import datetime as dt
import json
import sqlite3
import statistics
import sys
from collections import defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/data/trading.db"
THRESHOLD = 65
COOLDOWN_MIN = 30          # loop runs ~30s/cycle; ENTRY_COOLDOWN_SECONDS

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

rows = c.execute("""SELECT ts, detail_json FROM decisions
                    WHERE detail_json LIKE '%entry_score%' ORDER BY id""").fetchall()

events = []   # (ts, side, score, spread) — one entry per DISTINCT turn
for r in rows:
    try:
        d = json.loads(r["detail_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        continue
    es = d.get("entry_score") or {}
    total = es.get("total")
    if total is None or float(total) < THRESHOLD:
        continue
    curve = (d.get("stochastic") or {}).get("curve", "")
    side = "BUY" if curve == "TURNING_UP" else "SELL" if curve == "TURNING_DOWN" else None
    if side is None:
        continue
    ts = dt.datetime.fromisoformat(r["ts"])
    # Collapse repeats of the same direction into one event: a new event
    # starts only after the cooldown, or when the direction reverses.
    if events:
        last_ts, last_side, _, _ = events[-1]
        gap = (ts - last_ts).total_seconds() / 60.0
        if side == last_side and gap < COOLDOWN_MIN:
            continue
    events.append((ts, side, float(total), es.get("spread_points")))

if not events:
    print("  no qualifying distinct turns yet")
    sys.exit(0)

first, last = events[0][0], events[-1][0]
span_h = (last - first).total_seconds() / 3600.0
if span_h <= 0:
    print("  not enough span yet")
    sys.exit(0)

print("=" * 70)
print(f"DISTINCT TURN EVENTS  ({span_h:.1f}h, cooldown-collapsed at {COOLDOWN_MIN}m)")
print("=" * 70)
print(f"  qualifying turns       : {len(events)}")
print(f"  turns per hour         : {len(events)/span_h:.2f}")
per = defaultdict(int)
for _, s, _, _ in events:
    per[s] += 1
print(f"  by direction           : BUY {per['BUY']}  SELL {per['SELL']}")

# ---- flips: a qualifying turn that opposes the side we hold --------------
flips, held, gaps, last_flip = 0, None, [], None
held_since = None
hold_times = []
for ts, side, _, _ in events:
    if held is None:
        held, held_since = side, ts
        continue
    if side == held:
        continue
    flips += 1
    if last_flip:
        gaps.append((ts - last_flip).total_seconds() / 60.0)
    hold_times.append((ts - held_since).total_seconds() / 60.0)
    last_flip = ts
    held, held_since = side, ts

print()
print("=" * 70)
print("FLIP BEHAVIOUR (what the user actually asked about)")
print("=" * 70)
print(f"  flips in {span_h:.1f}h            : {flips}")
print(f"  projected flips/day     : {flips/span_h*24:.0f}")
print(f"  as a share of all turns : {flips/max(len(events),1)*100:.0f}%")
if gaps:
    print(f"  gap between flips       : median {statistics.median(gaps):.0f} min")
if hold_times:
    print(f"  how long each side held : median {statistics.median(hold_times):.0f} min")
if flips == 0:
    print("  -> no reversals observed; the turns have all been one-directional.")

# ---- cost ---------------------------------------------------------------
spreads = [e[3] for e in events if e[3]]
if spreads and flips:
    med = statistics.median(spreads)
    per_flip = med * 0.01 * 2 * 0.01 * 100      # pts→$ , 2 crossings, 0.01 lot, XAU contract 100
    print()
    print("=" * 70)
    print("COST OF THE FLIPPING")
    print("=" * 70)
    print(f"  median spread            : {med:.1f} pts")
    print(f"  round-trip cost per flip : ${per_flip:.4f}")
    print(f"  projected spread/day     : ${per_flip*flips/span_h*24:.2f}")
    print(f"  break-even move per flip : {med*0.01:.3f} USD of gold price movement")
    print()
    print(f"  To net $50/day you need to earn ${50 + per_flip*flips/span_h*24:.2f}")
    print(f"  gross => {50/(per_flip*flips/span_h*24):.0f}% win rate just to break even.")

print()
print("=" * 70)
print("LIVE P&L (only realised evidence)")
print("=" * 70)
t = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) p FROM trades WHERE ts_close IS NOT NULL").fetchone()
o = c.execute("SELECT COUNT(*) n FROM trades WHERE ts_close IS NULL").fetchone()[0]
print(f"  closed: {t['n']}  pnl: {t['p']:.2f}   open: {o}")
if t["n"] < 5:
    print("  -> too few closed trades to judge P&L; the numbers above are the")
    print("     honest estimate, not a result.")