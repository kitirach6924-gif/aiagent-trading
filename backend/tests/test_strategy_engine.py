import pytest

from app.core.strategy_engine import StrategyEngine
from app.core.strategies import StrategyError
from app.mt5.base import Candle, Price


def _candles(closes):
    return [Candle(time=str(i), open=c, high=c * 1.0005, low=c * 0.9995, close=c, tick_volume=100)
            for i, c in enumerate(closes)]


def _price(bid=2650.0, ask=2650.2, spread=20.0):
    return Price(symbol="XAUUSD", bid=bid, ask=ask, spread_points=spread, time="now")


def test_evaluation_returns_structured_signal(registry):
    engine = StrategyEngine(registry)
    closes = [2650 + (i * 0.5 if i % 3 else -i * 0.2) for i in range(120)]
    sig = engine.evaluate("v1.0", _price(), _candles(closes), spread_points=20.0)
    assert sig.signal in ("BUY", "SELL", "WAIT")
    assert sig.strategy_version == "v1.0"
    assert sig.timestamp
    assert isinstance(sig.conditions_met, list)
    assert isinstance(sig.conditions_failed, list)


def test_spread_filter_reports_but_does_not_block(registry):
    """SPREAD_FILTER is telemetry, not a gate.

    The equity-relative spread cap was removed deliberately — under leverage an
    equity-relative spread limit is not a meaningful risk control. The rule must
    still RECORD the reading so the number stays auditable, and must NOT block
    the signal. This test pins that contract so the cap is not silently restored.
    """
    engine = StrategyEngine(registry)
    reg = registry
    reg.create(version="v2.0", name="spread only", status="APPROVED",
               rules=[{"type": "EMA_CROSS", "params": {"fast": 3, "slow": 5}},
                      {"type": "SPREAD_FILTER", "params": {"max_points": 5}}], origin="test")
    closes = [2650 + i * 0.5 for i in range(120)]
    wide = engine.evaluate("v2.0", _price(spread=40), _candles(closes), spread_points=40)
    narrow = engine.evaluate("v2.0", _price(spread=3), _candles(closes), spread_points=3)

    # Not blocked, whatever the spread.
    assert not any("SPREAD" in f for f in wide.conditions_failed)
    assert not any("SPREAD" in f for f in narrow.conditions_failed)
    # But the reading is still recorded, so it stays visible to the operator.
    assert any("SPREAD_INFO(40pts)" in f for f in wide.conditions_met)
    assert any("SPREAD_INFO(3pts)" in f for f in narrow.conditions_met)


def test_registry_rejects_unknown_rule(fresh_db):
    from app.core.strategies import StrategyRegistry
    reg = StrategyRegistry(fresh_db)
    with pytest.raises(StrategyError):
        reg.validate_rules([{"type": "SHELL_EXEC", "params": {}}])


def test_approval_requires_human(fresh_db):
    from app.core.strategies import StrategyRegistry
    reg = StrategyRegistry(fresh_db)
    reg.create("v9.9", "x", [{"type": "EMA_CROSS", "params": {}}], origin="test")
    with pytest.raises(StrategyError):
        reg.set_status("v9.9", "APPROVED", approved_by=None)
    reg.set_status("v9.9", "APPROVED", approved_by="human@x")
    assert reg.status("v9.9") == "APPROVED"


def test_activate_requires_approved(registry):
    with pytest.raises(StrategyError):
        registry.set_active_version("v1.1")  # PROPOSAL cannot be activated
