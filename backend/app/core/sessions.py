"""Trading-session windows in UTC — the single source of truth.

Both the risk gate (app/core/risk.py) and the strategy engine
(app/core/strategy_engine.py) need to know which sessions are running. They
previously carried their own copy of the same table, which is how they drifted
and how a 17:00 UTC trade came to be reported as "session LONDON (hour 17 UTC)
not in allowed sessions" when NEWYORK was in fact active and permitted.

BOUNDS ARE HALF-OPEN: (start, end) means start <= hour < end. LONDON (7, 16)
therefore covers 07:00-15:59 UTC. A window whose start is greater than its end
wraps midnight (SYDNEY 21 -> 6).

These are rough windows, not exchange session times; they exist to gate when
the agent is willing to trade, not to describe market microstructure.
"""
from __future__ import annotations

SESSION_WINDOWS: dict[str, tuple[int, int]] = {
    "SYDNEY": (21, 6),
    "TOKYO": (0, 9),
    "LONDON": (7, 16),
    "NEWYORK": (12, 21),
}

# Reported when the clock falls outside every window above. Not a real session:
# it exists so a block names the actual situation instead of mislabelling the
# hour with whichever session happens to be the call-site default.
OFF_HOURS = "OFF_HOURS"


def in_window(hour_utc: int, window: tuple[int, int]) -> bool:
    """True when hour_utc falls inside the half-open (possibly wrapping) window."""
    start, end = window
    if start <= end:
        return start <= hour_utc < end
    return hour_utc >= start or hour_utc < end


def active_sessions(hour_utc: int) -> list[str]:
    """Every session running at hour_utc, in SESSION_WINDOWS declaration order."""
    return [name for name, window in SESSION_WINDOWS.items() if in_window(hour_utc, window)]


def current_session(hour_utc: int) -> str:
    """The session to gate on: the first active one, else OFF_HOURS.

    Where windows overlap (e.g. 12:00-15:59 is both LONDON and NEWYORK) the
    earlier-declared session wins. The caller still checks the result against
    the allowed list, so the choice only affects the label, not the decision.
    """
    active = active_sessions(hour_utc)
    return active[0] if active else OFF_HOURS
