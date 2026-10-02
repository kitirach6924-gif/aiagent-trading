"""Is the entry score gate reachable? Score the real scoring.py arithmetic.

ENTRY_THRESHOLD = 80. Weights are turn=65, trend=35, and zone/%D/spread are all
zero. So a pass needs 80 of a possible 100 — turn alone (65) can never do it;
trend must also fire. Check whether "trend fires" and "the stochastic actually
turned" can ever be true at the same time on the signal the loop proposes.

No DB, no network: pure arithmetic on the module's own constants.
"""
import sys

sys.path.insert(0, r"E:\forex\aiagent\backend")

from app.core import scoring as sc  # noqa: E402
from app.config import settings  # noqa: E402

print("=" * 70)
print("SCORING CONFIG (from the module itself)")
print("=" * 70)
print(f"  ENTRY_THRESHOLD   = {sc.ENTRY_THRESHOLD}")
print(f"  W_TURN_STRENGTH   = {sc.W_TURN_STRENGTH}")
print(f"  W_TREND           = {sc.W_TREND}")
print(f"  W_ZONE            = {sc.W_ZONE}   (removed at user request)")
print(f"  W_D_CROSS         = {sc.W_D_CROSS}")
print(f"  W_SPREAD          = {sc.W_SPREAD}   (removed at user request)")
print(f"  STRONG_TURN       = {sc.STRONG_TURN}")
print(f"  curve_min_slope   = {settings.curve_min_slope_change}")
print(f"  total available   = {sc.W_TURN_STRENGTH + sc.W_TREND}")

print()
print("=" * 70)
print("CAN THE GATE EVER BE REACHED?")
print("=" * 70)
turn_only = sc.W_TURN_STRENGTH
print(f"  turn alone (max)  = {turn_only}  -> passes {turn_only >= sc.ENTRY_THRESHOLD}")
print(f"  trend alone (max) = {sc.W_TREND}  -> passes {sc.W_TREND >= sc.ENTRY_THRESHOLD}")
print(f"  turn + trend      = {turn_only + sc.W_TREND}  -> passes {turn_only + sc.W_TREND >= sc.ENTRY_THRESHOLD}")
print()
if turn_only < sc.ENTRY_THRESHOLD:
    print("  >>> A setup with a perfect turn but NO trend alignment scores "
          f"{turn_only} and is REJECTED.")
    print("      The gate is reachable only when trend also fires.")

print()
print("=" * 70)
print("TREND RULE — the only condition that can add the missing 15+ points")
print("=" * 70)
print("  BUY  gets full 35 only when  close > EMA20")
print("  SELL gets full 35 only when  close < EMA20")
print("  otherwise 0")
print()
print("  The loop proposes a side from the STOCHASTIC CURVE state, not from")
print("  the EMA trend. A stochastic turn can fire while price sits on the")
print("  wrong side of EMA20 — e.g. curve TURNING_UP in a range that is still")
print("  below its EMA20. That combination is exactly 65/100 = WAIT.")

print()
print("=" * 70)
print("WHAT THE VPS ACTUALLY SAW")
print("=" * 70)
print("  134x  score gate: score 65/100 (threshold 80): turn=65")
print("        -> turn was PERFECT (65/65) and trend contributed 0.")
print("        -> 66 of the 134 were rejected solely on trend alignment.")
print()
print("  This is not a bug: it is the gate working as configured. The question")
print("  is whether the intended strategy wanted trend as a hard requirement.")
