"""Regression tests for the session gate that produced the Telegram spam:

    RISK_BLOCK  trading_session: session LONDON (hour 17 UTC) not in allowed sessions

Root cause: `check()` declared `session_name: str = "LONDON"` and no caller ever
passed it, so every hour was tested against the LONDON window. Hour 17 UTC is
inside NEWYORK (12:00-20:59) — a session that IS in RISK_ALLOWED_SESSIONS — yet
the trade was refused because it was not inside LONDON (07:00-15:59).

These tests pin the corrected contract: the active session is derived from the
clock, and a block message names the session that is actually active.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core.risk import RiskGate  # noqa: E402


def test_hour_17_is_newyork_not_london():
    """17 UTC is inside NEWYORK (12-20) and outside LONDON (7-16)."""
    assert "NEWYORK" in RiskGate._active_sessions(17)
    assert "LONDON" not in RiskGate._active_sessions(17)


def test_newyork_hour_is_allowed():
    """The exact blocked case from the log: 17 UTC, NEWYORK, is in the allowed list."""
    assert RiskGate._session_allowed(RiskGate, 17, "NEWYORK") is True


def test_off_hours_is_reported_as_off_hours_not_london():
    """No hour falls outside every window today, so OFF_HOURS is the guard for
    a narrowed config — it must not be silently labelled LONDON."""
    assert RiskGate._session_allowed(RiskGate, 17, "OFF_HOURS") is False


def test_session_name_defaults_to_none_not_london():
    """The default must not pin the label; risk.check() derives it instead."""
    import inspect

    default = inspect.signature(RiskGate.check).parameters["session_name"].default
    assert default is None, "session_name must be derived from the clock, not defaulted to LONDON"


def test_active_sessions_covers_all_24_hours():
    """Every hour belongs to at least one session, so no trade hour is a dead zone."""
    uncovered = [h for h in range(24) if not RiskGate._active_sessions(h)]
    assert uncovered == [], f"hours with no active session: {uncovered}"


def test_london_window_end_is_exclusive():
    assert RiskGate._session_allowed(RiskGate, 15, "LONDON") is True
    assert RiskGate._session_allowed(RiskGate, 16, "LONDON") is False


def test_sydney_window_wraps_midnight():
    """SYDNEY (21-6) crosses midnight; both ends of the wrap must be inside."""
    assert RiskGate._session_allowed(RiskGate, 23, "SYDNEY") is True
    assert RiskGate._session_allowed(RiskGate, 2, "SYDNEY") is True
    assert RiskGate._session_allowed(RiskGate, 12, "SYDNEY") is False


def test_block_message_names_the_active_session(fresh_db, env_demo):
    """A block must say which session is actually running, so the log is actionable."""
    from app.core.risk import RiskGate as RG
    from tests.test_risk_gate import _account, _price

    gate = RG(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2650.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=17)
    # 17 UTC is NEWYORK and NEWYORK is allowed by default, so the session
    # check must now PASS where it previously failed.
    assert "trading_session" not in d.checks_failed, (
        f"17 UTC is NEWYORK and should be allowed; got {d.checks_failed}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
