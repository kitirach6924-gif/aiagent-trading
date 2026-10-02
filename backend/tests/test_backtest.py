import pytest

from app.core.backtest import Backtester


@pytest.mark.asyncio
async def test_backtest_baseline_vs_proposal(registry):
    bt = Backtester(registry)
    result = await bt.run("v1.1", seed=42)
    assert result["baseline_version"] == "v1.0"
    assert "metrics" in result["baseline"] and "metrics" in result["proposal"]
    bm = result["baseline"]["metrics"]
    pm = result["proposal"]["metrics"]
    for m in (bm, pm):
        assert "trades" in m and "win_rate" in m and "total_pnl" in m and "max_drawdown" in m
    # metrics must be recorded in db
    rows = registry.db.query("SELECT * FROM backtests WHERE strategy_version='v1.1'")
    assert rows


@pytest.mark.asyncio
async def test_backtest_deterministic_with_seed(registry):
    bt = Backtester(registry)
    a = await bt.run("v1.1", seed=7)
    b = await bt.run("v1.1", seed=7)
    assert a["proposal"]["metrics"] == b["proposal"]["metrics"]
