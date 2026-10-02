"""Where does price actually reverse? Structure analysis on real candles.

The user asked where to bank profit and where the stop belongs. Numbers,
not preferences. Three questions, answered from the M5/M15 candles:

    1. How big are the swings? (leg size distribution)
    2. After an entry trigger, how far does price go FOR us before it
       goes against us? (MFE / MAE — the actual exit evidence)
    3. Does the stop distance implied by (1) survive the spread?

Everything here is measured. Where a level is a judgement call it is
labelled as one.
"""
import json
from collections import defaultdict
from statistics import median, mean

data = json.load(open("/tmp/candles.json"))


def swings(bars, lookback=2):
    """Fractal swing highs/lows: a bar that beats `lookback` bars each side."""
    hi, lo = [], []
    for i in range(lookback, len(bars) - lookback):
        window = bars[i - lookback:i + lookback + 1]
        if bars[i]["h"] == max(w["h"] for w in window):
            hi.append((i, bars[i]["h"]))
        if bars[i]["l"] == min(w["l"] for w in window):
            lo.append((i, bars[i]["l"]))
    return hi, lo


def atr(bars, period=14):
    """Average True Range — the volatility unit everything else is sized in."""
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


for TF in ("M5", "M15"):
    bars = data.get(TF, [])
    if len(bars) < 60:
        continue

    print()
    print("=" * 68)
    print(f"  {TF} STRUCTURE — {len(bars)} bars, "
          f"{bars[0]['t'][:16]} -> {bars[-1]['t'][:16]}")
    print("=" * 68)

    # ---- 1. leg sizes ---------------------------------------------------
    hi, lo = swings(bars)
    highs = sorted(h[1] for h in hi)
    lows = sorted(l[1] for l in lo)

    # Pair each swing HIGH with the nearest LATER swing LOW (one complete
    # down swing), and each swing LOW with the nearest LATER swing HIGH.
    # Both directions matter — an earlier version collapsed runs of same-kind
    # pivots and lost the up legs entirely.
    down_legs, up_legs = [], []
    used = set()
    for i, (hi_idx, hi_val) in enumerate(hi):
        for lo_idx, lo_val in lo:
            if lo_idx > hi_idx and lo_idx not in used:
                size = hi_val - lo_val
                if size > 0:
                    down_legs.append(size)
                    used.add(lo_idx)
                break
    used = set()
    for i, (lo_idx, lo_val) in enumerate(lo):
        for hi_idx, hi_val in hi:
            if hi_idx > lo_idx and hi_idx not in used:
                size = hi_val - lo_val
                if size > 0:
                    up_legs.append(size)
                    used.add(hi_idx)
                break

    trs = atr(bars)
    a = mean(trs[-200:]) if len(trs) >= 200 else mean(trs)

    print(f"\n  ATR(14)            : {a:.2f} points")
    print(f"  swing highs found  : {len(hi)}")
    print(f"  swing lows found   : {len(lo)}")

    for label, legs in (("UP legs", up_legs), ("DOWN legs", down_legs)):
        if not legs:
            continue
        legs_sorted = sorted(legs)
        p25 = legs_sorted[int(len(legs_sorted) * 0.25)]
        p50 = median(legs_sorted)
        p75 = legs_sorted[int(len(legs_sorted) * 0.75)]
        print(f"\n  {label} ({len(legs)}):")
        print(f"    p25 {p25:6.1f}   median {p50:6.1f}   p75 {p75:6.1f}")
        print(f"    in ATR: p25 {p25/a:.1f}x  median {p50/a:.1f}x  p75 {p75/a:.1f}x")

    all_legs = [x for x in (up_legs + down_legs) if x > 0]
    if all_legs:
        m = median(all_legs)
        print(f"\n  median leg overall : {m:.1f} points ({m/a:.1f}x ATR)")

    # ---- 2. MFE / MAE after a momentum trigger --------------------------
    # A practical proxy for "an entry fired": the stochastic turn the live
    # system uses. Approximated by a simple 3-bar momentum flip, then we
    # measure what followed.
    SPREAD = 55.0
    def eval_triggers(horizons):
        out = defaultdict(lambda: {"mfe": [], "mae": [], "net": []})
        for i in range(20, len(bars) - max(horizons)):
            if bars[i]["c"] <= bars[i - 3]["c"] and \
               bars[i - 1]["c"] > bars[i - 4]["c"]:
                side = "BUY"
            elif bars[i]["c"] >= bars[i - 3]["c"] and \
                 bars[i - 1]["c"] < bars[i - 4]["c"]:
                side = "SELL"
            else:
                continue
            entry = bars[i]["c"]
            for h in horizons:
                future = bars[i + 1:i + h + 1]
                if not future:
                    continue
                if side == "BUY":
                    mfe = max(f["h"] for f in future) - entry
                    mae = entry - min(f["l"] for f in future)
                else:
                    mfe = entry - min(f["l"] for f in future)
                    mae = max(f["h"] for f in future) - entry
                out[h]["mfe"].append(mfe)
                out[h]["mae"].append(mae)
                # net if held the full horizon and paid the spread once
                out[h]["net"].append(mfe - SPREAD)
        return out

    horizons = [3, 6, 12, 24]
    res = eval_triggers(horizons)

    print(f"\n  AFTER A TRIGGER — how far it went for us, and against us")
    print(f"  (points, net = MFE minus the 55pt spread)")
    print(f"    {'horizon':<10}{'n':<6}{'med MFE':>10}{'med MAE':>10}"
          f"{'med net':>10}{'MFE>cost':>11}")
    for h in horizons:
        if h not in res or not res[h]["mfe"]:
            continue
        mfe = res[h]["mfe"]
        mae = res[h]["mae"]
        net = res[h]["net"]
        beat = 100 * sum(1 for x in net if x > 0) / len(net)
        print(f"    {str(h)+' bar'+('s' if h>1 else ''):<10}{len(mfe):<6}"
              f"{median(mfe):>10.1f}{median(mae):>10.1f}{median(net):>10.1f}"
              f"{beat:>10.0f}%")

    # ---- 3. the level that matters --------------------------------------
    print(f"\n  THE COST LINE: spread = {SPREAD:.0f} points "
          f"= {SPREAD/a:.1f}x ATR, {SPREAD/median(all_legs):.1f}x the median leg")
    print(f"  A take-profit must sit beyond {SPREAD:.0f} points or the trade")
    print(f"  cannot pay for itself. Median leg is "
          f"{median(all_legs) if all_legs else 0:.1f} points,")
    m = median(all_legs) if all_legs else 0
    if m <= SPREAD:
        print("  which is INSIDE the cost — the median swing cannot pay for a flip.")
    else:
        pct = 100 * len([l for l in all_legs if l > SPREAD]) / len(all_legs)
        print(f"  so only {pct:.0f}% of swings clear the cost.")