import pytest

from app.core.risk import RiskGate
from app.mt5.base import AccountStatus, Price
from app.mt5.simulator import SimulatorConnector


def _price(spread=20.0, bid=2649.9, ask=2650.1):
    return Price(symbol="XAUUSD", bid=bid, ask=ask, spread_points=spread, time="now")


def _account(equity=10000.0, balance=10000.0):
    return AccountStatus(login="t", name="t", currency="USD", balance=balance, equity=equity,
                         margin_used=0, leverage=100, server="s", mode="DEMO")


def test_pass_when_all_ok(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", session_name="LONDON", now_hour_utc=10)
    assert d.allowed, d.checks_failed


def test_block_emergency_stop(fresh_db, env_demo):
    fresh_db.kv_set("emergency_stop", True)
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "emergency_stop" in d.checks_failed


def test_block_paused(fresh_db, env_demo):
    fresh_db.kv_set("paused", True)
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "paused" in d.checks_failed


def test_block_missing_sl(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=None, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "stop_loss_required" in d.checks_failed


def test_block_max_lot(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 5.0, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "max_lot" in d.checks_failed


def test_risk_percent_is_managed_externally(fresh_db, env_demo):
    """Position-risk management is explicitly the operator's job, outside the app.

    The user removed the 1%-of-equity risk rule: under leverage an equity-relative
    risk percentage is not a meaningful control, and sizing is managed manually.
    The gate must therefore NOT block on risk percent, and — because a
    `max_risk_percent` setting still exists in config.py — that setting must not
    silently come back to life as a gate. This test pins the absence.
    """
    gate = RiskGate(fresh_db)
    # 0.1 lot XAUUSD, SL 100 USD away => 1000 USD risk = 10% of 10k equity.
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2550.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert "max_risk_percent" not in d.checks_failed, (
        "risk-percent gating was removed deliberately; the gate must not reintroduce it")

    # And the orphan config field must not be read as a live control.
    import app.core.risk as risk_mod
    src = open(risk_mod.__file__, encoding="utf-8").read()
    assert "max_risk_percent" not in src, (
        "risk.py references max_risk_percent again; risk gating is operator-managed")


def test_spread_is_reported_but_not_enforced(fresh_db, env_demo):
    """Spread is informational only.

    The equity-relative spread cap was removed on purpose: with leverage an
    equity-relative limit is not a meaningful risk control, and the user manages
    position size outside the platform. The gate must not reintroduce it.
    """
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(spread=999), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert "spread_limit" not in d.checks_failed, (
        "the spread cap was removed deliberately; the gate must not reintroduce it")


def test_block_max_positions(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    from app.mt5.base import Position
    positions = [Position(ticket=str(i), symbol="XAUUSD", side="BUY", lot=0.01, entry_price=1,
                          current_price=1, pnl=0, sl=0, tp=0, opened_at="") for i in range(3)]
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=positions, strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "max_open_positions" in d.checks_failed


def test_block_consecutive_losses(fresh_db, env_demo):
    for i in range(3):
        fresh_db.execute(
            "INSERT INTO trades (ts_open, ts_close, symbol, side, lot, pnl, strategy_version, mode) "
            "VALUES (?,?,?,?,?,?,?,?)", (f"t{i}", f"c{i}", "XAUUSD", "BUY", 0.1, -5.0, "v1.0", "DEMO"))
    gate = RiskGate(fresh_db)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "max_consecutive_losses" in d.checks_failed


def test_block_daily_loss(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    gate.set_day_start_balance(10000.0)
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(),
                   account=_account(equity=9500.0, balance=10000.0),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "max_daily_loss" in d.checks_failed


def test_block_symbol_permission(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    d = gate.check("EURUSD", "BUY", 0.1, sl=1.08, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed and "symbol_permission" in d.checks_failed


def test_blocks_are_logged(fresh_db, env_demo):
    gate = RiskGate(fresh_db)
    gate.check("XAUUSD", "BUY", 99.0, sl=2648.0, price=_price(), account=_account(),
               positions=[], strategy_version="v1.0", now_hour_utc=10)
    rows = fresh_db.query("SELECT * FROM risk_blocks")
    assert rows, "every BLOCK must be recorded"


def test_fail_closed_on_internal_error(fresh_db, env_demo, monkeypatch):
    gate = RiskGate(fresh_db)
    monkeypatch.setattr(gate, "consecutive_losses", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    d = gate.check("XAUUSD", "BUY", 0.1, sl=2648.0, price=_price(), account=_account(),
                   positions=[], strategy_version="v1.0", now_hour_utc=10)
    assert not d.allowed
