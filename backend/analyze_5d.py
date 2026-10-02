"""The 73 real MT5 deals, analysed against M5 vs M15.

WHAT THIS ANSWERS
    The user asked: over the last 5 days, at which timeframe would we have
    banked more profit — M5 or M15? These are real broker deals, not a
    simulation, so the gross P&L is settled fact.

WHAT IT CANNOT ANSWER
    Which timeframe WOULD have produced those trades. Every one of these
    deals was opened by the live system on M5. There is no counterfactual
    M15 run to compare against, so the M15 side must be estimated, not
    measured — and that estimate is labelled as such everywhere.
"""
import json
from collections import defaultdict
from datetime import datetime

data = json.load(open("/tmp/candles.json"))
hist = data.get("_history", [])

if not hist:
    raise SystemExit("no history pulled")

print(f"  deals analysed: {len(hist)}")

# ---- normalise ------------------------------------------------------------
deals = []
for h in hist:
    ts = (h.get("time") or h.get("ts") or h.get("close_time")
          or h.get("open_time") or h.get("date") or "")
    deals.append({
        "ts": ts,
        "side": h.get("side"),
        "pnl": float(h.get("pnl", 0) or 0),
        "entry": float(h.get("entry_price", 0) or 0),
        "exit": float(h.get("exit_price", 0) or 0),
        "ticket": h.get("ticket"),
    })

print(f"  sample ts values: {[d['ts'] for d in deals[:3]]}")


def parse_ts(v):
    if isinstance(v, (int, float)):
        return datetime.utcfromtimestamp(v)
    s = str(v).replace("Z", "+00:00")
    for fmt in (None,):
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(str(v)[:19], f)
        except ValueError:
            continue
    return None


dated = [(parse_ts(d["ts"]), d) for d in deals]
dated = [(t, d) for t, d in dated if t is not None]
dated.sort(key=lambda x: x[0])

if dated:
    print(f"  date range: {dated[0][0]} -> {dated[-1][0]}")
    span_days = (dated[-1][0] - dated[0][0]).total_seconds() / 86400
    print(f"  span: {span_days:.2f} days")
else:
    span_days = 7.0
    print("  (no timestamps in the payload — using the broker window)")

# MT5's history payload here carries no timestamp field, so a strict date
# filter would silently drop every deal and report a confident $0.00. Fall
# back to the whole pull and SAY that the window is the broker's, not ours.
if dated:
    recent = dated
    window_basis = "deal timestamps"
else:
    recent = [(None, d) for d in deals]
    window_basis = "broker's 7-day history (deals carry no timestamp field)"

# ---- the settled numbers --------------------------------------------------
pnls = [d["pnl"] for _, d in recent]
gross = sum(pnls)
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]

print()
print("=" * 62)
print("  REALISED RESULT — what the account actually earned")
print("=" * 62)
print(f"  trades      : {len(pnls)}")
print(f"  window      : {window_basis}")
print(f"  gross P&L   : ${gross:+.2f}")
print(f"  wins        : {len(wins)}/{len(pnls)}  ({100*len(wins)/len(pnls) if pnls else 0:.0f}%)")
if wins:
    print(f"  avg win     : ${sum(wins)/len(wins):+.2f}")
if losses:
    print(f"  avg loss    : ${sum(losses)/len(losses):+.2f}")
if pnls:
    wl = (sum(wins)/len(wins)) / abs(sum(losses)/len(losses)) if losses else 0
    print(f"  win/loss ratio: {wl:.2f}")
print(f"  per day     : ${gross/max(span_days,0.01):+.2f}  over {span_days:.1f} days")

# ---- by side --------------------------------------------------------------
print()
print("  by side:")
by = defaultdict(list)
for _, d in recent:
    by[d["side"]].append(d["pnl"])
for side, ps in sorted(by.items(), key=lambda kv: -sum(kv[1])):
    print(f"    {str(side):<5} n={len(ps):<3} gross ${sum(ps):+8.2f}   "
          f"wins {sum(1 for p in ps if p > 0)}/{len(ps)}")

# ---- the cost question ----------------------------------------------------
SPREAD_POINTS = 55.0
POINT_VALUE, CONTRACT, LOT = 0.01, 100.0, 0.01
COST = SPREAD_POINTS * POINT_VALUE * 2 * LOT * CONTRACT

print()
print("=" * 62)
print("  THE SPREAD QUESTION")
print("=" * 62)
print(f"  round-trip flip cost : ${COST:.2f}")
print(f"  trades taken         : {len(pnls)}")
print(f"  total cost of churn  : ${len(pnls)*COST:.2f}")
print(f"  gross                : ${gross:+.2f}")
print(f"  NET after churn      : ${gross - len(pnls)*COST:+.2f}")
if gross > 0:
    print(f"  costs ate            : {100*len(pnls)*COST/gross:.0f}% of gross profit")
print()
if gross > 0 and gross < len(pnls) * COST:
    print("  VERDICT: the trades were profitable individually, but not")
    print("           profitable after paying for every flip.")
elif gross > 0:
    print("  VERDICT: profitable even after paying for every flip.")
else:
    print("  VERDICT: not profitable at this cadence.")

# ---- what M15 would have looked like -------------------------------------
print()
print("=" * 62)
print("  M15 COUNTERFACTUAL (estimate, not measured)")
print("=" * 62)

m15 = data.get("M15", [])
m5 = data.get("M5", [])
print(f"  M15 bars available : {len(m15)}")
print(f"  M5 bars available  : {len(m5)}")

if m15:
    # A trend-following read of the higher timeframe: how much of the move
    # actually happened in runs vs noise.
    moves = []
    for i in range(1, len(m15)):
        moves.append(abs(m15[i]["c"] - m15[i - 1]["c"]))
    if moves:
        moves_sorted = sorted(moves)
        med = moves_sorted[len(moves_sorted) // 2]
        p90 = moves_sorted[int(len(moves_sorted) * 0.9)]
        print(f"  M15 |move|: median {med:.2f} pts, p90 {p90:.2f} pts")
        print(f"  a single M15 bar covers a median {med:.1f} points of travel")
        print(f"  flip cost = {SPREAD_POINTS:.0f} points = "
              f"{SPREAD_POINTS/med:.1f}x a median M15 bar")

        # M15 flip frequency is ~1/3 of M5
        est_flips_15 = len(pnls) / 3.0
        est_cost_15 = est_flips_15 * COST
        print()
        print(f"  if the same logic ran on M15:")
        print(f"    flips    ~{est_flips_15:.0f}  (M5 does ~3x the flips)")
        print(f"    cost     ~${est_cost_15:.2f}")
        print(f"    but each entry has 3x the time to develop, so the")
        print(f"    per-trade gross would need to be ~{COST*3:.2f} to break even")

# ---- the honest bottom line ---------------------------------------------
print()
print("=" * 62)
print("  WHAT I CAN AND CANNOT SAY")
print("=" * 62)
print("  CAN SAY:")
print(f"    - these {len(pnls)} deals are real and settled")
print(f"    - the account made ${gross:+.2f} gross on them")
print(f"    - after paying ${len(pnls)*COST:.2f} of churn, net is "
      f"${gross-len(pnls)*COST:+.2f}")
print("  CANNOT SAY:")
print("    - what an M15 run would have earned. No M15 trade history exists.")
print("      The M15 figure above is arithmetic on bar sizes, not a result.")
print("  TO KNOW FOR SURE:")
print("    run the same rules on M15 in parallel and compare the two books")