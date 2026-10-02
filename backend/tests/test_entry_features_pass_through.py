"""Regression test: the order path must never NameError on entry_features.

THE BUG
    `_execute()` takes `entry_features` and reads it on every BUY/SELL path:

        await self._execute(symbol, version, d.action, d.lot, d.sl, d.tp, risk,
                            reason_codes=d.reason_codes, entry_features=entry_features)

    but `_act_on_decision()` — the only caller — had no such local variable and
    no module-level global of that name. The V1 caller already passed
    `entry_features=entry_features` into `_act_on_decision`, so the intent was
    obvious and the signature was simply never updated to accept it.

    Consequence: the NameError fired AFTER the risk gate had already passed
    every check. The order was never sent to the broker, and the caller saw a
    bare LOOP_ERROR with no signal that an approved trade had been dropped.
    This is the same failure shape as the earlier sl=None bridge TypeError:
    journal says risk=PASS, zero trades, no trace.

THE FIX
    `_act_on_decision(..., entry_features=None)` forwards it explicitly.

WHAT THIS TEST PINS
    A BUY decision that clears the risk gate reaches the connector. No MT5, no
    network: the connector is a stub, so the assertion is purely about the
    Python control flow that used to explode.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.autonomous_agent import AgentDecision  # noqa: E402
from app.core.loop import TradingLoop  # noqa: E402
from app.core.risk import RiskDecision  # noqa: E402
from app.core.stochastic import Curve  # noqa: E402


class _OpenResult:
    ok = True
    ticket = "TICKET-1"
    price = 2650.0


class _Connector:
    """Minimal connector stub — records opens, never touches a broker."""

    def __init__(self):
        self.opened = []

    async def get_positions(self):
        return []

    async def get_price(self, symbol):
        return None

    async def get_account_status(self):
        return None

    async def get_history(self, days=2):
        return []

    async def open_position(self, **kwargs):
        self.opened.append(kwargs)
        return _OpenResult()

    async def close_position(self, ticket):
        return _OpenResult()


class _Journal:
    def record_open(self, **kwargs):
        pass

    def record_close(self, **kwargs):
        pass


def _buy_decision():
    # sl is supplied deliberately: _execute() is fail-closed and refuses to send
    # an order without a stop when RISK_STOP_LOSS_REQUIRED is on. This test is
    # about the entry_features hand-off, not about the SL policy.
    return AgentDecision(
        action="BUY",
        symbol="XAUUSD",
        curve="TURNING_UP",
        reasoning_summary="stochastic turn up",
        lot=0.01,
        sl=2600.0,
    )


@pytest.mark.asyncio
async def test_buy_that_passes_risk_gate_reaches_the_connector(fresh_db):
    """The regression: this raised NameError before the fix.

    A passing risk decision must end in a broker call. If it does not, the
    trade was silently dropped after being approved.
    """
    connector = _Connector()
    loop_ = TradingLoop(fresh_db, connector, None, _Journal(), None)

    async def _allow(*args, **kwargs):
        return RiskDecision(allowed=True, checks_passed=["all"])

    loop_._risk_check = _allow
    loop_._recently_fired = lambda *a, **k: False

    await loop_._act_on_decision("XAUUSD", "v2.0", _buy_decision())

    assert len(connector.opened) == 1, (
        "approved BUY never reached open_position — the order was dropped "
        "after passing the risk gate"
    )
    assert connector.opened[0]["side"] == "BUY"


@pytest.mark.asyncio
async def test_entry_features_are_forwarded_to_the_journal(fresh_db):
    """Features captured at entry must survive the hand-off to _execute."""
    connector = _Connector()
    recorded = {}

    class _J:
        def record_open(self, **kwargs):
            recorded.update(kwargs)

        def record_close(self, **kwargs):
            pass

    loop_ = TradingLoop(fresh_db, connector, None, _J(), None)

    async def _allow(*args, **kwargs):
        return RiskDecision(allowed=True)

    loop_._risk_check = _allow
    loop_._recently_fired = lambda *a, **k: False

    features = {"curve": "TURNING_UP", "score": 72.0}
    await loop_._act_on_decision("XAUUSD", "v2.0", _buy_decision(),
                                 entry_features=features)

    assert recorded.get("features") == features, (
        "entry features were dropped — the adaptive tuner would have nothing "
        "to learn from"
    )


@pytest.mark.asyncio
async def test_missing_features_falls_back_to_reason_codes(fresh_db):
    """Default None must degrade gracefully, not raise — CLOSE/flip use it."""
    connector = _Connector()
    recorded = {}

    class _J:
        def record_open(self, **kwargs):
            recorded.update(kwargs)

        def record_close(self, **kwargs):
            pass

    loop_ = TradingLoop(fresh_db, connector, None, _J(), None)

    async def _allow(*args, **kwargs):
        return RiskDecision(allowed=True)

    loop_._risk_check = _allow
    loop_._recently_fired = lambda *a, **k: False

    await loop_._act_on_decision("XAUUSD", "v2.0", _buy_decision())

    assert recorded.get("features") == {"reason_codes": []}