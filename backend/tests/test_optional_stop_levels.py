"""Regression test: sl=None must not kill the order.

THE BUG
    `trade_open` did `sl = float(args["sl"])`. With AUTONOMOUS_NO_SL=True the
    loop deliberately sends sl=None, so float(None) raised
    `TypeError: float() argument must be a string or a real number, not 'NoneType'`
    inside the bridge. The order never reached the broker, no TRADE_OPEN event
    fired, and the loop journal still said risk=PASS. 16 such decisions produced
    zero trades and left no trace.

Verified against the live bridge: a probe with lot=0.0/sl=None returned exactly
that TypeError, while get_status reported trade_allowed=true on a DEMO account.

These tests drive the argument handling directly; the MT5 module is stubbed, so
no order is ever sent.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.mt5 import mcp_tools  # noqa: E402

class _SendResult:
    """MetaTrader5 returns a namedtuple with retcode/order/comment."""
    retcode = 10009      # TRADE_RETCODE_DONE
    order = 1
    deal = 1
    volume = 0.01
    price = 2650.2
    comment = "Done"




# ---------------------------------------------------------------- _opt_float
@pytest.mark.parametrize("value", [None, "", 0, 0.0, "0"])
def test_absent_stop_levels_become_none(value):
    """MT5's 'no stop level' is 0.0; the parser normalises it to None."""
    assert mcp_tools._opt_float(value, "sl") is None


def test_real_stop_level_survives():
    assert mcp_tools._opt_float(2640.5, "sl") == 2640.5
    assert mcp_tools._opt_float("2640.5", "sl") == 2640.5


def test_garbage_stop_level_is_rejected_clearly():
    with pytest.raises(ValueError) as e:
        mcp_tools._opt_float("not-a-price", "sl")
    assert "invalid sl" in str(e.value)


# -------------------------------------------------- the original crash itself
def test_trade_open_no_longer_raises_on_none_sl(monkeypatch):
    """The exact failure: sl=None. Before the fix this raised TypeError."""
    captured = {}

    class _Tick:
        ask, bid = 2650.2, 2650.0

    class _SI:
        visible, trade_stops_level, point = True, 5.0, 0.01
        digits = 2

    monkeypatch.setattr(mcp_tools, "ensure_mt5", lambda: None)
    monkeypatch.setattr(mcp_tools, "round_price", lambda sym, p: p or 0.0)
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info", lambda s: _SI())
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info_tick", lambda s: _Tick())
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_BUY", 0)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_SELL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "TRADE_ACTION_DEAL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TIME_GTC", 0)

    def _fake_send(req):
        captured.update(req)
        return _SendResult()

    monkeypatch.setattr(mcp_tools.mt5, "order_send", _fake_send)

    # sl and tp both absent — what AUTONOMOUS_NO_SL=True produces.
    out = mcp_tools.trade_open(
        {"symbol": "XAUUSD", "side": "BUY", "lot": 0.01, "sl": None, "tp": None})

    assert out, "trade_open must return a result, not raise"
    assert "sl" in captured, "the order must actually reach order_send"
    assert captured["sl"] == 0.0, "MT5 needs 0.0 for no stop, not None"
    assert captured["tp"] == 0.0


def test_order_send_reached_with_zeroed_stops(monkeypatch):
    """Regression guard on the request dict itself, independent of MT5 replies."""
    captured = {}

    class _Tick:
        ask, bid = 2650.2, 2650.0

    class _SI:
        visible = True
        digits = 2
        point = 0.01
        trade_stops_level = 5.0

    monkeypatch.setattr(mcp_tools, "ensure_mt5", lambda: None)
    monkeypatch.setattr(mcp_tools, "round_price", lambda sym, p: p or 0.0)
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info", lambda s: _SI())
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info_tick", lambda s: _Tick())
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_BUY", 0)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_SELL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "TRADE_ACTION_DEAL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TIME_GTC", 0)
    monkeypatch.setattr(mcp_tools.mt5, "order_send",
                        lambda r: (captured.update(r), _SendResult())[1])

    mcp_tools.trade_open(
        {"symbol": "XAUUSD", "side": "SELL", "lot": 0.01, "sl": None})
    assert captured["volume"] == 0.01
    assert captured["sl"] == 0.0


def test_missing_sl_key_also_works(monkeypatch):
    """args.get() means an absent key is fine too, not just an explicit None."""
    class _Tick:
        ask, bid = 2650.2, 2650.0

    class _SI:
        visible = True
        digits = 2
        point = 0.01
        trade_stops_level = 5.0

    captured = {}
    monkeypatch.setattr(mcp_tools, "ensure_mt5", lambda: None)
    monkeypatch.setattr(mcp_tools, "round_price", lambda sym, p: p or 0.0)
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info", lambda s: _SI())
    monkeypatch.setattr(mcp_tools.mt5, "symbol_info_tick", lambda s: _Tick())
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_BUY", 0)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TYPE_SELL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "TRADE_ACTION_DEAL", 1)
    monkeypatch.setattr(mcp_tools.mt5, "ORDER_TIME_GTC", 0)
    monkeypatch.setattr(mcp_tools.mt5, "order_send",
                        lambda r: (captured.update(r), _SendResult())[1])

    mcp_tools.trade_open({"symbol": "XAUUSD", "side": "BUY", "lot": 0.01})
    assert captured["sl"] == 0.0
    assert captured["tp"] == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
