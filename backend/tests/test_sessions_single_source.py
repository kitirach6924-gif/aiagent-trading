"""The session table must exist in exactly one place.

Both the risk gate and the strategy engine previously carried their own copy of
the same windows, which is how they drifted and produced
"session LONDON (hour 17 UTC) not in allowed sessions" while NEWYORK was active
and permitted. The table now lives in app/core/sessions.py; these tests stop it
being copied back out.
"""
import ast
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import sessions  # noqa: E402
from app.core.risk import RiskGate  # noqa: E402

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_table_exists_once():
    assert sessions.SESSION_WINDOWS == {
        "SYDNEY": (21, 6), "TOKYO": (0, 9), "LONDON": (7, 16), "NEWYORK": (12, 21),
    }


def test_no_module_redefines_the_windows():
    """A hardcoded copy of the table outside sessions.py is the bug returning."""
    offenders = []
    for py in APP.rglob("*.py"):
        if py.name == "sessions.py" or "__pycache__" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if {"SYDNEY", "LONDON", "NEWYORK"} <= set(keys):
                offenders.append(str(py.relative_to(APP)))
                break
    assert not offenders, f"session table redefined in: {offenders}"


def test_risk_gate_uses_the_shared_table():
    assert RiskGate.SESSION_WINDOWS is sessions.SESSION_WINDOWS
    assert RiskGate.OFF_HOURS == sessions.OFF_HOURS


def test_engine_and_gate_agree_on_every_hour():
    """The two consumers must classify all 24 hours identically."""
    from app.core.strategy_engine import StrategyEngine  # noqa: F401
    for hour in range(24):
        assert RiskGate._active_sessions(hour) == sessions.active_sessions(hour)


def test_current_session_matches_the_gate_default():
    for hour in range(24):
        expected = sessions.active_sessions(hour)
        want = expected[0] if expected else sessions.OFF_HOURS
        assert sessions.current_session(hour) == want


def test_hour_17_is_newyork_everywhere():
    """The exact hour from the Telegram report."""
    assert sessions.current_session(17) == "NEWYORK"
    assert RiskGate._session_allowed(RiskGate, 17, "NEWYORK") is True


def test_off_hours_is_denied_when_sessions_configured():
    from app.config import settings
    if settings.allowed_sessions:
        assert RiskGate._session_allowed(RiskGate, 17, sessions.OFF_HOURS) is False


def test_wrap_window_works_both_sides_of_midnight():
    assert sessions.in_window(23, sessions.SESSION_WINDOWS["SYDNEY"]) is True
    assert sessions.in_window(2, sessions.SESSION_WINDOWS["SYDNEY"]) is True
    assert sessions.in_window(12, sessions.SESSION_WINDOWS["SYDNEY"]) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
