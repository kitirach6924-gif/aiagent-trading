"""Regression tests for Telegram RISK_BLOCK spam.

The channel showed the identical block four times in nine minutes:

    ⛔ RISK_BLOCK
    trading_session: session LONDON (hour 17 UTC) not in allowed sessions
    {'symbol': 'XAUUSD', 'failed': {...}}

A standing block re-publishes on every loop iteration and the notifier had no
suppression, so the same line landed once per cycle and buried anything new.

These tests drive on_event with send() stubbed — no network, no token.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.events import Event  # noqa: E402
from app.integrations.telegram import TelegramNotifier  # noqa: E402

REASON = "session LONDON (hour 17 UTC) not in allowed sessions"


def _notifier(window=300.0):
    n = TelegramNotifier(token="t", chat_id="c", dedup_window_seconds=window)
    sent = []
    n.send = lambda text: sent.append(text) or True
    return n, sent


def _block(reason=REASON):
    return Event(action="RISK_BLOCK", result=reason, agent="risk",
                 payload={"symbol": "XAUUSD", "failed": {"trading_session": reason}})


def test_repeated_identical_block_sends_once():
    n, sent = _notifier()
    for _ in range(4):
        asyncio.run(n.on_event(_block()))
    assert len(sent) == 1, f"expected 1 message for 4 identical blocks, got {len(sent)}"


def test_suppressed_count_is_reported_on_next_send():
    """A block that is still standing must not look resolved when it reappears."""
    n, sent = _notifier(window=0.0001)
    for _ in range(3):
        asyncio.run(n.on_event(_block()))
        import time
        time.sleep(0.001)
    assert len(sent) >= 2, "window expiry should let a message through again"
    assert "suppressed" in sent[-1], f"re-send must disclose suppressed count: {sent[-1]!r}"


def test_different_reason_is_not_suppressed():
    """Two different blocks are two different facts — both must be delivered."""
    n, sent = _notifier()
    asyncio.run(n.on_event(_block("reason A")))
    asyncio.run(n.on_event(_block("reason B")))
    assert len(sent) == 2


def test_different_action_is_not_suppressed():
    n, sent = _notifier()
    asyncio.run(n.on_event(_block()))
    asyncio.run(n.on_event(Event(action="TRADE_OPEN", result="opened", agent="exec", payload={})))
    assert len(sent) == 2


def test_unimportant_actions_are_still_ignored():
    n, sent = _notifier()
    asyncio.run(n.on_event(Event(action="HEARTBEAT", result="tick", agent="loop", payload={})))
    assert sent == []


def test_zero_window_disables_dedup():
    """Opt-out path: RISK_DEDUP_WINDOW_SECONDS=0 must restore every message."""
    n, sent = _notifier(window=0)
    for _ in range(3):
        asyncio.run(n.on_event(_block()))
    assert len(sent) == 3


def test_dedup_map_is_bounded():
    """A high-cardinality stream must not grow the map without limit."""
    n, sent = _notifier(window=600.0)
    n._dedup_max_keys = 10
    for i in range(50):
        asyncio.run(n.on_event(_block(f"reason {i}")))
    assert len(n._recent) <= 10, f"map grew to {len(n._recent)}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
