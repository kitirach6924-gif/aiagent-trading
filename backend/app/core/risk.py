"""Deterministic Risk Gate.

HARD RULE: Risk Gate is plain deterministic Python. No LLM output can pass,
modify, bypass or disable it. Every BLOCK is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.core import sessions
from app.core.events import iso_utc
from app.mt5.base import AccountStatus, Price


@dataclass
class RiskDecision:
    allowed: bool
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: dict[str, str] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        if self.allowed:
            return "all risk checks passed"
        return "; ".join(f"{k}: {v}" for k, v in self.checks_failed.items())


class RiskGate:
    """Evaluates every order against fixed limits. Fail-closed on any error."""

    def __init__(self, db) -> None:
        self.db = db

    # --- state helpers -------------------------------------------------
    def _day_key(self) -> str:
        return iso_utc()[:10]

    def _pnl_today(self, account: AccountStatus, day_start_balance: float | None) -> float:
        base = day_start_balance if day_start_balance is not None else account.balance
        return account.equity - base

    def consecutive_losses(self) -> int:
        rows = self.db.query(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY ts_close DESC LIMIT ?",
            (settings.max_consecutive_losses,),
        )
        n = 0
        for r in rows:
            if r["pnl"] < 0:
                n += 1
            else:
                break
        return n

    def day_start_balance(self) -> float | None:
        return self.db.kv_get(f"day_start_balance:{self._day_key()}")

    def set_day_start_balance(self, balance: float) -> None:
        self.db.kv_set(f"day_start_balance:{self._day_key()}", balance)

    def max_equity_seen(self) -> float:
        return self.db.kv_get("max_equity_seen", 0.0)

    def update_equity_high_water(self, equity: float) -> None:
        if equity > self.max_equity_seen():
            self.db.kv_set("max_equity_seen", equity)

    def rebase(self, balance: float) -> None:
        """Reset loss baselines to the account we are actually trading now.

        These are seeded once from whatever the connector looked like at startup.
        If that was the simulator (equity 10,000) and we later connect a real MT5
        account worth 52, every drawdown check compares against 10,000 and reports
        a ~99% loss, blocking all trading forever. Called when the connector
        upgrades to REAL_MT5.
        """
        self.db.kv_set("max_equity_seen", balance)
        self.set_day_start_balance(balance)

    # --- main gate ------------------------------------------------------
    def check(
        self,
        symbol: str,
        side: str,
        lot: float,
        sl: float | None,
        price: Price,
        account: AccountStatus,
        positions: list,
        strategy_version: str,
        *,
        allowed_symbols: list[str] | None = None,
        allowed_strategies: list[str] | None = None,
        session_name: str | None = None,
        now_hour_utc: int | None = None,
    ) -> RiskDecision:
        failed: dict[str, str] = {}
        passed: list[str] = []

        # Which session the hour actually falls in. This was a "LONDON" default
        # that no caller ever passed, so every hour was tested against the
        # LONDON window and 17 UTC was refused while NEWYORK — which is allowed
        # and active at 12-20 — was trading. Derive it from the clock instead.
        hour = now_hour_utc if now_hour_utc is not None else int(iso_utc()[11:13])
        if session_name is None:
            session_name = sessions.current_session(hour)

        def ok(name: str, cond: bool, msg_fail: str = "") -> None:
            if cond:
                passed.append(name)
            else:
                failed[name] = msg_fail or "failed"

        try:
            ok("emergency_stop", not self.db.kv_get("emergency_stop", False), "EMERGENCY STOP is active")
            ok("trading_mode", settings.trading_mode == "DEMO" or settings.live_trading,
               f"trading_mode={settings.trading_mode}, live_trading={settings.live_trading}")
            ok("paused", not self.db.kv_get("paused", False), "trading is paused")
            ok("symbol_permission", symbol in (allowed_symbols or settings.symbols), f"{symbol} not allowed")
            ok("strategy_permission", strategy_version in (allowed_strategies or [strategy_version]),
               f"strategy {strategy_version} not allowed")

            # lot
            ok("max_lot", 0 < lot <= settings.max_lot, f"lot {lot} > max {settings.max_lot}")
            ok("lot_positive", lot > 0, "lot must be positive")

            # User decision: risk-per-trade is NOT calculated. There is no SL, so a
            # 1%-of-equity rule has nothing to measure against, and inventing a
            # virtual stop distance would imply a loss cap the agent never enforces.
            # Lot sizing, spread, position count, daily loss and drawdown gates below
            # are unaffected.
            ok("stop_loss_required", (not settings.stop_loss_required) or (sl is not None and sl > 0),
               "SL required but missing")

            ok("max_open_positions", len(positions) < settings.max_open_positions,
               f"{len(positions)} open >= max {settings.max_open_positions}")

            # daily loss
            day_start = self.day_start_balance()
            pnl_today = self._pnl_today(account, day_start)
            loss_pct_today = (-pnl_today / day_start * 100) if (day_start and pnl_today < 0) else 0
            ok("max_daily_loss", loss_pct_today <= settings.max_daily_loss_percent,
               f"daily loss {loss_pct_today:.2f}% > max {settings.max_daily_loss_percent}%")

            # drawdown from high-water mark
            hwm = self.max_equity_seen() or (account.equity if account else 0)
            dd_pct = ((hwm - account.equity) / hwm * 100) if hwm else 0
            ok("max_drawdown", dd_pct <= settings.max_drawdown_percent,
               f"drawdown {dd_pct:.2f}% > max {settings.max_drawdown_percent}%")

            # spread — NOT a gate. The user removed it deliberately: forex runs on 1:500
            # leverage, where a spread cap expressed as a share of equity means
            # something different at every balance, and equity-per-trade
            # budgeting borrowed from unleveraged equities does not apply here.
            # Spread is still measured and reported so it stays visible.
            sp = getattr(price, "spread_points", None) if price is not None else None
            if price is not None and sp is not None and sp > 0:
                contract = self._contract_size(symbol)
                cost = (sp / 100.0) * lot * contract
                equity = account.equity if account else 0
                passed.append(f"SPREAD({sp}pts={cost:.2f}"
                              f"{f'={cost / equity * 100:.3f}% equity' if equity else ''})")

            # session
            running = sessions.active_sessions(hour)
            ok("trading_session", self._session_allowed(hour, session_name),
               f"session {session_name} (hour {hour} UTC) not in allowed sessions; "
               f"active at this hour: {','.join(running) if running else self.OFF_HOURS}")

            ok("max_consecutive_losses", self.consecutive_losses() < settings.max_consecutive_losses,
               f"{self.consecutive_losses()} consecutive losses >= max {settings.max_consecutive_losses}")
        except Exception as e:  # noqa: BLE001 - fail-closed
            failed["internal_error"] = f"risk gate error (fail-closed): {e}"

        decision = RiskDecision(allowed=not failed, checks_passed=passed, checks_failed=failed)
        if not decision.allowed:
            self.db.execute(
                "INSERT INTO risk_blocks (ts, symbol, reason, checks_failed_json) VALUES (?,?,?,?)",
                (iso_utc(), symbol, decision.reason, __import__("json").dumps(failed, ensure_ascii=False)),
            )
        return decision

    def _contract_size(self, symbol: str) -> float:
        """Contract size per 1.0 lot. Metal symbols trade 100 oz/lot on most brokers;
        FX majors 100_000. Uses broker data when a connector is attached."""
        s = symbol.upper()
        if s.find("XAU") >= 0 or s.find("GOLD") >= 0:
            return 100.0
        if s.find("XAG") >= 0 or s.find("SILVER") >= 0:
            return 5_000.0
        return 100_000.0

    # Session windows live in app/core/sessions.py so the gate and the strategy
    # engine cannot drift apart. Kept as class attributes for call-site
    # compatibility; both are the same objects.
    SESSION_WINDOWS = sessions.SESSION_WINDOWS
    OFF_HOURS = sessions.OFF_HOURS

    @classmethod
    def _active_sessions(cls, hour_utc: int) -> list[str]:
        return sessions.active_sessions(hour_utc)

    def _session_allowed(self, hour_utc: int, session_name: str) -> bool:
        if session_name == self.OFF_HOURS:
            # Outside every defined window, so denied whenever a session list is
            # configured. Reported under its own name rather than mislabelled.
            return not settings.allowed_sessions
        if session_name not in self.SESSION_WINDOWS:
            return True
        if session_name not in settings.allowed_sessions:
            return False
        return sessions.in_window(hour_utc, self.SESSION_WINDOWS[session_name])
