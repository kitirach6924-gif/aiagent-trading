"""What is the real break-even, and is 55 points actually the spread?

The 55-point figure has been carried through every analysis. It came from
a single quote (bid 4155.15 / ask 4155.70). One quote at one moment is not
a spread measurement. If the true typical spread is smaller, the swing
rule is closer to viable than the last run suggested; if it is larger, the
conclusion strengthens.

Method: run the swing rule across a RANGE of assumed spreads and show
where it breaks even. That tells us what spread we would need, and we can
then compare that to the broker's actual quotes.
"""
import json
from statistics import mean, median

data = json.load(open("/tmp/candles.json"))
POINT_VALUE, CONTRACT, LOT = 0.01, 100.0, 0.01

# spread is paid ONCE per round trip (measured live: 55 pts, 12 samples).
# The earlier x2 was wrong.


def pivots(bars, lb=2):
    hi, lo = [], []
    for i in range(lb, len(bars) - lb):
        w = bars[i - lb:i + lb + 1]
        if bars[i]["h"] >= max(x["h"] for x in w):
            hi.append((i, bars[i]["h"]))
        if bars[i]["l"] <= min(x["l"] for x in w):
            lo.append((i, bars[i]["l"]))
    return hi, lo


def swings(bars, lb=2):
    hi, lo = pivots(bars, lb)
    seq = sorted([(i, "H", v) for i, v in hi] + [(i, "L", v) for i, v in lo])
    alt = []
    for idx, kind, val in seq:
        if not alt or alt[-1][1] != kind:
            alt.append((idx, kind, val))
    return alt


def simulate(bars, alt, lb=2, spread=55.0):
    """Gross result of the rule, before spread. Spread applied by caller."""
    out = []
    for i in range(len(alt) - 1):
        (ai, ak, av), (bi, bk, bv) = alt[i], alt[i + 1]
        ei = bi + lb
        if ei >= len(bars):
            continue
        if bk == "L":
            side, target, leg = "BUY", av, av - bv
        else:
            side, target, leg = "SELL", av, bv - av
        if leg <= 0:
            continue
        half, entry = leg / 2.0, bars[ei]["c"]
        armed, exitp, done = False, None, False
        for k in range(ei, len(bars)):
            b = bars[k]
            fav = (b["h"] - entry) if side == "BUY" else (entry - b["l"])
            if (side == "BUY" and b["h"] >= target) or \
               (side == "SELL" and b["l"] <= target):
                exitp, done = target, True
                break
            if not armed and fav >= half:
                armed = True
            if armed:
                if (side == "BUY" and b["l"] <= entry) or \
                   (side == "SELL" and b["h"] >= entry):
                    exitp, done = entry, True
                    break
        if exitp is None:
            exitp = bars[-1]["c"]
        out.append(((exitp - entry) if side == "BUY" else (entry - exitp), side))
    return out


for TF in ("M5", "M15"):
    bars = data.get(TF, [])
    if len(bars) < 120:
        continue
    alt = swings(bars)
    trades = simulate(bars, alt)
    gross = [g for g, _ in trades]

    print()
    print("=" * 70)
    print(f"  {TF} — gross result of the swing rule, before any spread")
    print("=" * 70)
    print(f"  trades        : {len(trades)}")
    print(f"  gross/trade   : {mean(gross):+.2f} pts")
    print(f"  median gross  : {median(gross):+.2f} pts")
    print(f"  best/worst    : {max(gross):+.1f} / {min(gross):+.1f} pts")
    pos = len([g for g in gross if g > 0])
    print(f"  positive gross: {pos}/{len(gross)} ({100*pos/len(gross):.0f}%)")

    # WHY is gross negative at all? Median is positive, so a few very large
    # losses are dragging the mean. Show that split rather than just the mean.
    be = mean(gross)
    srt = sorted(gross)
    losers = [g for g in gross if g < 0]
    print(f"\n  where the loss comes from:")
    print(f"    losers    : {len(losers)} trades, avg {mean(losers):+.1f} pts, "
          f"total {sum(losers):+.1f}")
    winners_g = [g for g in gross if g > 0]
    print(f"    winners   : {len(winners_g)} trades, avg {mean(winners_g):+.1f} pts, "
          f"total {sum(winners_g):+.1f}")
    print(f"    worst 5   : {[round(x,1) for x in srt[:5]]}")
    print(f"    best 5    : {[round(x,1) for x in srt[-5:]]}")
    if losers:
        print(f"    the {len(losers)} losers cost {abs(sum(losers)):.0f} pts while "
              f"the {len(winners_g)} winners return only {sum(winners_g):.0f}")
    print(f"\n  BREAK-EVEN SPREAD = {be:.1f} points")
    print(f"  (the strategy only wins if the true spread is below this)")
    print(f"\n  {'assumed spread':>16}{'net/trade':>14}{'total $':>12}{'verdict':>14}")
    for sp in (10, 20, 30, 40, 55, 70):
        net_pts = be - sp
        tot = sum(g - sp for g in gross) * POINT_VALUE * LOT * CONTRACT
        verdict = "PROFIT" if net_pts > 0 else "loss"
        print(f"  {sp:>14}{net_pts:>+14.2f}{tot:>+12.2f}{verdict:>14}")

    print()
    print(f"  broker quote taken earlier: bid 4155.15 / ask 4155.70 = 55 pts")
    print(f"  if 55 is real, the swing rule loses "
          f"${sum(g-55 for g in gross)*POINT_VALUE*LOT*CONTRACT:+.2f} on this sample")
    print(f"  the rule needs the spread under {be:.0f} points to work at all")