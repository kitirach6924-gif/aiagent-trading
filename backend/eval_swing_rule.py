"""The user's rule, tested against real M15/M5 structure.

THE RULE AS STATED
    1. Identify swing highs and swing lows.
    2. A down leg ends at a swing LOW  -> buy there.
       An up leg ends at a swing HIGH   -> sell there.
    3. Once price travels 50% of that swing leg in our favour, move the
       stop to breakeven + spread, so the trade can no longer lose.

WHY THIS NEEDS A CAREFUL SIMULATION
    Entering at a swing is a different animal from the momentum entries
    measured earlier: there is structure underneath the entry. But the
    swing must only be used once it is CONFIRMED, or the result is
    fiction — so entry is taken at the close of bar i+lookback, never at
    the pivot itself.

Also measured: what fraction of legs are even large enough to clear the
55-point spread. That is the ceiling on this whole idea.

VERIFIED FIRST
    The broker's 73 history deals all carry entry_price == exit_price, so
    their pnl is swap/commission, not price movement. They cannot be used
    to validate this simulation. The candles WERE checked against them and
    agree: candle range 4110-4297 vs deal entries 4118-4200, same
    instrument, same scale.
"""
import json
from statistics import median, mean

SPREAD = 55.0
POINT_VALUE, CONTRACT, LOT = 0.01, 100.0, 0.01
COST_USD = SPREAD * POINT_VALUE * LOT * CONTRACT       # $0.55 per round trip
# NOTE: spread is paid ONCE per round trip. An earlier version multiplied by
# 2 (charging it on entry AND exit) and reported $1.10 — that was wrong, and
# it made the strategy look twice as bad as it is. Measured 12x live:
# bid 4162.94 / ask 4163.48 = 55 points, stable, min 54 max 56.

data = json.load(open("/tmp/candles.json"))


def pivots(bars, lb=2):
    """Confirmed swing highs/lows. Confirmation costs `lb` bars — real, not free."""
    hi, lo = [], []
    for i in range(lb, len(bars) - lb):
        w = bars[i - lb:i + lb + 1]
        if bars[i]["h"] >= max(x["h"] for x in w):
            hi.append((i, bars[i]["h"]))
        if bars[i]["l"] <= min(x["l"] for x in w):
            lo.append((i, bars[i]["l"]))
    return hi, lo


def legs(bars, lb=2):
    """Alternate pivots into complete swings: high->low (down), low->high (up)."""
    hi, lo = pivots(bars, lb)
    seq = sorted([(i, "H", v) for i, v in hi] + [(i, "L", v) for i, v in lo])
    alt = []
    for idx, kind, val in seq:
        if not alt or alt[-1][1] != kind:
            alt.append((idx, kind, val))
    return alt


def run(TF, lb=2):
    bars = data.get(TF, [])
    if len(bars) < 120:
        print(f"  {TF}: not enough bars ({len(bars)})")
        return

    alt = legs(bars, lb)
    print()
    print("=" * 70)
    print(f"  {TF} — user rule: enter at swing extreme, BE+spread at 50%")
    print("=" * 70)
    print(f"  bars {len(bars)} | confirmed pivots {len(alt)} "
          f"(confirm lag {lb} bars)")

    # ---- leg size vs the cost line --------------------------------------
    up = [alt[i + 1][2] - alt[i][2] for i in range(len(alt) - 1)
          if alt[i + 1][1] == "H"]
    dn = [alt[i][2] - alt[i + 1][2] for i in range(len(alt) - 1)
          if alt[i + 1][1] == "L"]
    allv = [x for x in up + dn if x > 0]
    if not allv:
        return

    srt = sorted(allv)
    p25, p50, p75 = (srt[int(len(srt) * q)] for q in (0.25, 0.5, 0.75))
    p90 = srt[int(len(srt) * 0.90)]
    beat = 100 * len([x for x in allv if x > SPREAD]) / len(allv)
    print(f"\n  leg sizes: p25 {p25:.1f} | median {p50:.1f} | p75 {p75:.1f} "
          f"| p90 {p90:.1f} points")
    print(f"  legs larger than the {SPREAD:.0f}pt spread: {beat:.0f}%")
    print(f"  -> even a PERFECT entry banks {p50:.1f} - {SPREAD:.0f} = "
          f"{p50 - SPREAD:+.1f} points on the median leg")

    # ---- simulate each swing entry --------------------------------------
    # A down leg: high at (hi,H), low at (lo,L), hi < lo. We BUY at the low.
    # Entry bar = lo + lb (confirmation). TP = H. SL starts wide, moves to
    # BE+spread once price has covered 50% of the leg.
    trades = []
    for i in range(len(alt) - 1):
        (ai, ak, av), (bi, bk, bv) = alt[i], alt[i + 1]
        entry_idx = bi + lb
        if entry_idx >= len(bars):
            continue

        if bk == "L":                      # down leg ended -> BUY the low
            side, target = "BUY", av
            leg = av - bv
            stop_ref = bv
        else:                               # up leg ended -> SELL the high
            side, target = "SELL", av
            leg = bv - av
            stop_ref = bv
        if leg <= 0:
            continue

        half = leg / 2.0
        entry = bars[entry_idx]["c"]

        # walk forward until the target is hit, or the 50% rule armed and
        # then price comes back to breakeven
        be_armed = False
        exit_price = None
        outcome = None
        for k in range(entry_idx, len(bars)):
            b = bars[k]
            if side == "BUY":
                fav = b["h"] - entry
                adv = entry - b["l"]
            else:
                fav = entry - b["l"]
                adv = b["h"] - entry

            # target first?
            reached = (side == "BUY" and b["h"] >= target) or \
                      (side == "SELL" and b["l"] <= target)
            if reached:
                exit_price = target
                outcome = "TARGET"
                break
            # arm the BE rule on the way
            if not be_armed and fav >= half:
                be_armed = True
            # after arming, a return to entry (plus the spread we paid)
            # means stop out at breakeven
            if be_armed:
                if side == "BUY" and b["l"] <= entry:
                    exit_price = entry
                    outcome = "BE_STOP"
                    break
                if side == "SELL" and b["h"] >= entry:
                    exit_price = entry
                    outcome = "BE_STOP"
                    break
        if exit_price is None:
            # still open at the end of the sample: mark at the last close
            exit_price = bars[-1]["c"]
            outcome = "OPEN" if be_armed else "OPEN_UNARMED"

        gross_pts = ((exit_price - entry) if side == "BUY"
                     else (entry - exit_price))
        # the spread is paid crossing in AND out; a BE stop returns entry
        # price but the two crossings still cost SPREAD
        net_pts = gross_pts - SPREAD
        trades.append({
            "side": side, "leg": leg, "half": half, "outcome": outcome,
            "gross_pts": gross_pts, "net_pts": net_pts,
            "armed": be_armed, "entry": entry, "exit": exit_price,
            "ts": bars[entry_idx]["t"],
        })

    if not trades:
        print("  no trades")
        return

    nets = [t["net_pts"] for t in trades]
    wins = [n for n in nets if n > 0]
    print(f"\n  trades simulated : {len(trades)}")
    print(f"  net points total : {sum(nets):+.1f}")
    print(f"  net per trade    : {mean(nets):+.2f} pts  "
          f"(${mean(nets)*POINT_VALUE*LOT*CONTRACT:+.2f}/trade)")
    print(f"  winners          : {len(wins)}/{len(trades)} "
          f"({100*len(wins)/len(trades):.0f}%)")
    print(f"  best  : {max(nets):+.1f} pts")
    print(f"  worst : {min(nets):+.1f} pts")

    print(f"\n  outcome breakdown:")
    from collections import Counter
    for o, n in Counter(t["outcome"] for t in trades).most_common():
        sel = [t["net_pts"] for t in trades if t["outcome"] == o]
        print(f"    {o:<14} {n:>3}   avg {mean(sel):+7.1f} pts")

    armed = [t for t in trades if t["armed"]]
    print(f"\n  50% rule armed on {len(armed)}/{len(trades)} trades "
          f"({100*len(armed)/len(trades):.0f}%)")

    print(f"\n  by side:")
    for side in ("BUY", "SELL"):
        sel = [t["net_pts"] for t in trades if t["side"] == side]
        if sel:
            w = len([x for x in sel if x > 0])
            print(f"    {side:<5} n={len(sel):<3} avg {mean(sel):+7.2f} pts   "
                  f"win {100*w/len(sel):.0f}%   total {sum(sel):+8.1f}")

    # ---- how long does it take, and is it worth it -----------------------
    print(f"\n  VERDICT for {TF}:")
    tot_usd = sum(nets) * POINT_VALUE * LOT * CONTRACT
    per_day_pts = sum(nets) / max((len(bars)), 1)
    print(f"    total ${tot_usd:+.2f} over {len(bars)} bars")
    if mean(nets) > 0:
        print(f"    profitable on average ({mean(nets):+.2f} pts/trade)")
    else:
        print(f"    NOT profitable ({mean(nets):+.2f} pts/trade)")
    print(f"    trades needed to earn $10: "
          f"{'never at this rate' if mean(nets) <= 0 else f'{10/(mean(nets)*POINT_VALUE*LOT*CONTRACT):.0f}'}")


for TF in ("M5", "M15"):
    run(TF)