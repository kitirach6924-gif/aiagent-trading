"""Backtest engine. Deterministic; runs a strategy version over simulated history
and reports factual metrics only — never picks a winner for the user."""
from __future__ import annotations

import random
from dataclasses import asdict

from app.config import settings
from app.core.events import Event, bus, iso_utc
from app.core.strategy_engine import StrategyEngine
from app.mt5.simulator import atr, ema, rsi


def _gen_history(symbol: str, n: int = 1500, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    price = 2650.0 if symbol == "XAUUSD" else 1.1
    out = []
    drift = 0.02
    for i in range(n):
        step = rng.gauss(drift / n, 0.0012) * price
        o = price
        c = price + step
        h = max(o, c) + abs(rng.gauss(0, 0.0006 * price))
        lo = min(o, c) - abs(rng.gauss(0, 0.0006 * price))
        out.append({"time": i, "open": o, "high": h, "low": lo, "close": c})
        price = c
    return out


class Backtester:
    def __init__(self, registry) -> None:
        self.registry = registry
        self.engine = StrategyEngine(registry)

    async def run(self, version: str, symbol: str | None = None, baseline: str | None = None,
                  seed: int | None = None) -> dict:
        symbol = symbol or settings.symbols[0]
        baseline = baseline or _base_of(version)
        await bus.publish(Event(action="BACKTEST_STARTED", result=version, agent="backtest", strategy_version=version))
        try:
            base_metrics = self._run_single(baseline, symbol, seed)
            prop_metrics = self._run_single(version, symbol, seed)
            result = {
                "strategy_version": version,
                "baseline_version": baseline,
                "baseline": {"metrics": base_metrics},
                "proposal": {"metrics": prop_metrics},
                "ts": iso_utc(),
            }
            self.db_save(version, baseline, "COMPLETED", result)
            await bus.publish(Event(action="BACKTEST_COMPLETED", result=version, agent="backtest",
                                    strategy_version=version, payload={"metrics": prop_metrics}))
            return result
        except Exception as e:  # noqa: BLE001
            self.db_save(version, baseline, "FAILED", {"error": str(e)})
            await bus.publish(Event(action="BACKTEST_COMPLETED", result=version, agent="backtest",
                                    strategy_version=version, payload={"error": str(e)}))
            raise

    def db_save(self, version: str, baseline: str, status: str, detail: dict) -> None:
        import json
        db = getattr(self.registry, "db", None)
        if db is None:
            return
        db.execute(
            "INSERT INTO backtests (ts, strategy_version, status, metrics_json, baseline_version, detail_json) "
            "VALUES (?,?,?,?,?,?)",
            (iso_utc(), version, status,
             json.dumps(detail.get("proposal", {}).get("metrics", {}), ensure_ascii=False),
             baseline, json.dumps(detail, ensure_ascii=False, default=str)),
        )

    def _run_single(self, version: str, symbol: str, seed: int | None) -> dict:
        from app.core.strategies import StrategyError
        try:
            self.registry.rules(version)
        except StrategyError as e:
            return {"error": str(e), "trades": 0}

        candles = _gen_history(symbol, 1500, seed)
        start_equity = settings.backtest_start_equity
        equity = start_equity
        peak = equity
        max_dd = 0.0
        trades: list[float] = []
        position = None  # (side, entry, sl, tp)
        closes = [c["close"] for c in candles]

        i = 60
        while i < len(candles) - 1:
            window = candles[max(0, i - 60): i + 1]
            candle_objs = [
                type("C", (), {"time": c["time"], "open": c["open"], "high": c["high"],
                               "low": c["low"], "close": c["close"], "tick_volume": 0})()
                for c in window
            ]
            price = type("P", (), {"symbol": symbol, "bid": candles[i]["close"], "ask": candles[i]["close"],
                                   "spread_points": 20.0, "time": str(candles[i]["time"])})()
            sig = self.engine.evaluate(version, price, candle_objs, spread_points=20.0, now_hour_utc=12)

            if position is None and sig.signal in ("BUY", "SELL"):
                sl_dist = sig.context.get("sl_distance") or 0.002 * price.ask
                entry = price.ask if sig.signal == "BUY" else price.bid
                sl = entry - sl_dist if sig.signal == "BUY" else entry + sl_dist
                tp = entry + 1.5 * sl_dist if sig.signal == "BUY" else entry - 1.5 * sl_dist
                position = {"side": sig.signal, "entry": entry, "sl": sl, "tp": tp}
            elif position is not None:
                bar = candles[i]
                hit_sl = (position["side"] == "BUY" and bar["low"] <= position["sl"]) or \
                         (position["side"] == "SELL" and bar["high"] >= position["sl"])
                hit_tp = (position["side"] == "BUY" and bar["high"] >= position["tp"]) or \
                         (position["side"] == "SELL" and bar["low"] <= position["tp"])
                if hit_sl or hit_tp:
                    exit_price = position["sl"] if hit_sl else position["tp"]
                    diff = (exit_price - position["entry"]) if position["side"] == "BUY" else (position["entry"] - exit_price)
                    contract = 100.0 if symbol == "XAUUSD" else 100_000.0
                    pnl = diff * settings.max_lot * contract
                    trades.append(pnl)
                    equity += pnl
                    peak = max(peak, equity)
                    max_dd = min(max_dd, equity - peak)
                    position = None
            i += 1

        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        return {
            "trades": len(trades),
            "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0,
            "total_pnl": round(sum(trades), 2),
            "final_equity": round(equity, 2),
            "max_drawdown": round(max_dd, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        }


def _base_of(version: str) -> str:
    import re
    m = re.match(r"v(\d+)\.(\d+)", version)
    if m and int(m.group(2)) > 0:
        return f"v{m.group(1)}.0"
    return version
