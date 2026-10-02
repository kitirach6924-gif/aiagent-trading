"""Score is the single decider, and a flip closes and reopens in one cycle.

WHAT CHANGED (2026-10-01, user-confirmed)
    The user runs without SL: "use opening the other side to close this one,
    instead of SL." So the exit is not the curve, it is the score:

      score(proposed side) >= 65  ->  close the opposite position, open this side

    Previously the loop closed on the curve and returned. The new side was only
    reconsidered 30s later, and that reconsideration still went through the
    score gate — so a valid flip could leave the book flat indefinitely.

    Score and curve are not independent here: W_TURN_STRENGTH is the full 65
    points and score_setup rejects a curve that opposes the side, so score >= 65
    can only be reached when the stochastic has genuinely turned that way.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core.loop import close_side_name  # noqa: E402
from app.core.scoring import ENTRY_THRESHOLD, W_TREND, W_TURN_STRENGTH  # noqa: E402


def _loop_source():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent /
            "app/core/loop.py").read_text(encoding="utf-8")


# ---------------------------------------------------- the threshold identity
def test_threshold_still_65():
    assert ENTRY_THRESHOLD == 65


def test_full_turn_alone_is_enough_to_flip():
    """A decisive turn scores exactly the threshold, so flips are reachable."""
    assert W_TURN_STRENGTH == ENTRY_THRESHOLD


def test_trend_is_only_a_bonus():
    assert W_TREND == 35, "trend adds to a passing score, it is not required"


# -------------------------------------------- which position a flip closes
@pytest.mark.parametrize("proposed,expected", [
    ("BUY", "SELL"),
    ("SELL", "BUY"),
])
def test_flip_closes_the_opposite_side(proposed, expected):
    """The core rule: opening one side is the instruction to close the other."""
    assert close_side_name(proposed) == expected


def test_flip_is_symmetric():
    """Whichever side is open, the other one is what gets closed."""
    assert close_side_name("SELL") == "BUY"
    assert close_side_name("BUY") == "SELL"


# ------------------------------------------------- the loop structure itself
def test_opposite_open_branch_is_score_gated():
    """A flip must require score.passed, not merely a curve turn."""
    src = _loop_source()
    i = src.index("if opposite_open:")
    seg = src[i:i + 700]
    assert "if not score.passed:" in seg, (
        "the flip must be gated on score, per the user's rule")
    assert "holding" in seg, "a weak turn must hold the existing position"


def test_flip_reopens_in_the_same_cycle():
    """After closing, the loop must fall through to the entry — not return."""
    src = _loop_source()
    i = src.index("if opposite_open:")
    j = src.index("if not score.passed:", i)
    seg = src[j:j + 2000]
    assert '"flip": True' in seg, "the close must be tagged as a flip"
    assert "get_positions()" in seg, (
        "positions must be re-read after the close or the new entry looks "
        "like a duplicate")
    # The critical bit: no `return` after the close within the flip branch.
    after_close = seg[seg.index('"flip": True'):]
    before_next_gate = after_close.split("if not score.passed:")[-1] \
        if "if not score.passed:" in after_close else after_close
    tail = before_next_gate[:400]
    assert "return" not in tail, (
        "returning after the close would delay the new entry to the next cycle")


def test_handle_close_is_side_aware():
    src = _loop_source()
    assert "p.side == close_side" in src, (
        "_handle_close must not filter on a hardcoded side")


def test_duplicate_protection_still_present():
    src = _loop_source()
    assert "DUPLICATE_BUY_BLOCKED" in _autonomous_source()
    assert "DUPLICATE_SELL_BLOCKED" in _autonomous_source()


def _autonomous_source():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent /
            "app/core/autonomous_agent.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))