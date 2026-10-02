"""FastAPI app: REST + WebSocket for the Firebase-hosted dashboard.

The trading loop itself runs here on the VPS process — never inside Firebase Hosting.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import urllib.request

from fastapi import (Depends, FastAPI, HTTPException, Request, Response,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware

from app.ai.chat_agent import ChatAgent
from app.ai.model_router import router
from app.api.auth import SESSION_COOKIE, require_user
from app.api.totp_auth import AuthService, otpauth_uri
import app.api.auth as auth_module
from app.api.realtime import (account_snapshot_loop, event_forwarder,
                              make_heartbeat_loop, market_tick_loop)
from app.api.ws import manager
from app.config import settings
from app.core.agents import DecisionAgent, MarketAgent, StrategyAgent
from app.core.audit import AuditLog
from app.core.backtest import Backtester
from app.core.db import Database
from app.core.events import Event, bus
from app.core.journal import StatisticsEngine, TradeJournal
from app.core.loop import TradingLoop
from app.core.risk import RiskGate
from app.core.strategies import StrategyRegistry
from app.integrations.firestore_mirror import mirror
from app.integrations.telegram import TelegramNotifier
from app.mt5.factory import make_connector
from app.mt5.reconnect import ConnectorSupervisor

app = FastAPI(title="Autonomous AI Trading Agent", version="0.1.0")

# Titan command platform — single page, no build step, served by the same API so it
# Platform UI. The pages inherit the backend's auth — /login gates the console
# when PLATFORM_AUTH_ENABLED is on, and the API rejects every call without a
# valid session cookie, so hiding the page is cosmetic, the API is the real gate.
try:
    from fastapi.staticfiles import StaticFiles
    _platform_dir = os.path.join(os.path.dirname(__file__), "static", "platform")
    if os.path.isdir(_platform_dir):
        app.mount("/platform", StaticFiles(directory=_platform_dir, html=True), name="platform")
except Exception:  # noqa: BLE001
    pass

# ---------------- Login page ----------------
@app.get("/login")
async def login_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(
        os.path.dirname(__file__), "static", "platform", "login.html"))


@app.get("/")
async def root():
    """Send the browser to the console, or to the login screen when gated."""
    from fastapi.responses import RedirectResponse
    if settings.platform_auth_enabled and not settings.auth_disabled:
        return RedirectResponse("/login")
    return RedirectResponse("/platform/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- wiring ----
db = Database(settings.db_path)
platform_auth = AuthService(db)
auth_module.platform_auth = platform_auth
router.attach_db(db)
audit = AuditLog(db)
registry = StrategyRegistry(db)
connector, connector_source = make_connector()
mt5_supervisor = ConnectorSupervisor(connector, connector_source)
journal = TradeJournal(db, registry)
stats = StatisticsEngine(db)
risk_gate = RiskGate(db)
notifier = TelegramNotifier()
notifier.subscribe()
backtester = Backtester(registry)
chat = ChatAgent(db, connector, registry, journal, stats, notifier,
                 loop_ref=lambda: loop, backtester_ref=lambda: backtester)
decision_agent = DecisionAgent(connector, StrategyAgent(_engine := __import__(
    "app.core.strategy_engine", fromlist=["StrategyEngine"]).StrategyEngine(registry), registry), registry)
loop = TradingLoop(db, connector, registry, journal, risk_gate, notifier)
loop.bind_agents(decision_agent)


async def _audit_and_broadcast(event: Event) -> None:
    audit.record_event(event)
    await event_forwarder(event)
    await mirror.handle_event(event)


bus.subscribe("*", _audit_and_broadcast)

_rt_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup() -> None:
    # seed default strategy if registry is empty
    if not registry.list():
        registry.create(
            version="v1.0", name="EMA Trend + Filters (default)", status="APPROVED",
            rules=[
                {"type": "EMA_CROSS", "params": {"fast": 9, "slow": 21}},
                {"type": "RSI_FILTER", "params": {"period": 14, "oversold": 35, "overbought": 65}},
                {"type": "SPREAD_FILTER", "params": {"max_points": settings.max_spread_points}},
                {"type": "ATR_STOP", "params": {"period": 14, "mult": 2.0}},
                {"type": "SESSION_FILTER", "params": {"allowed": settings.allowed_sessions}},
            ],
            origin="seed", created_by="system")
    # seed Strategy V1 (Stochastic Curve + Chart Context) — spec §4/§24
    if not registry.get("v2.0"):
        registry.create(
            version="v2.0", name="STRATEGY_V1_STOCHASTIC_CURVE v1.0.0", status="APPROVED",
            rules=[
                {"type": "STOCHASTIC_CURVE", "params": {
                    "k_period": settings.stoch_k_period,
                    "d_period": settings.stoch_d_period,
                    "slowing": settings.stoch_slowing,
                    "min_slope_change": settings.curve_min_slope_change,
                    "confirmation_bars": settings.curve_confirmation_bars,
                    "smoothing": settings.curve_smoothing,
                }},
            ],
            origin="seed", created_by="system")
    if registry.status("v2.0") not in ("PRODUCTION",):
        # PRODUCTION sorts first in active_version() — deterministic autonomous default
        registry.set_status("v2.0", "PRODUCTION", approved_by="system")
    if registry.active_version() != "v2.0":
        registry.set_active_version("v2.0")
    # ---- knowledge registry entry (knowledge pack §38) ----
    from app.core.price_action import KNOWLEDGE_ID, KNOWLEDGE_VERSION
    entry = {
        "knowledge_id": KNOWLEDGE_ID, "version": KNOWLEDGE_VERSION,
        "category": "chart_analysis", "status": "ACTIVE",
        "purpose": ["candlestick_analysis", "market_structure", "support_resistance",
                    "breakout_analysis", "retest_analysis", "price_location", "chart_context"],
        "standalone_strategy": False, "can_trade_directly": False,
        "used_by": ["STRATEGY_V1_STOCHASTIC_CURVE", "chart_analysis", "trade_reasoning",
                    "backtest_analysis"],
        "validation": {"lookahead_bias_protection": True, "deterministic_features": True,
                       "ai_output_schema_validation": True},
    }
    known_raw = db.kv_get("knowledge_registry", "") or ""
    known: list = []
    if known_raw:
        try:
            parsed = json.loads(known_raw)
            if isinstance(parsed, str):        # older double-encoded value
                parsed = json.loads(parsed)
            known = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            known = []
    if not any(k.get("knowledge_id") == KNOWLEDGE_ID and k.get("version") == KNOWLEDGE_VERSION
               for k in known):
        known.append(entry)
        db.kv_set("knowledge_registry", json.dumps(known, ensure_ascii=False))
        await bus.publish(Event(action="KNOWLEDGE_REGISTERED", result=f"{KNOWLEDGE_ID} v{KNOWLEDGE_VERSION}",
                                agent="system", payload=entry))
    loop.start()
    # Baselines seeded from the simulator must be rebased onto the real account even
    # when the bridge was already up at import (the common case after a restart).
    if connector_source == "REAL_MT5":
        try:
            acc = await connector.get_account_status()
            if acc and acc.balance > 0:
                loop.risk_gate.rebase(acc.balance)
                await bus.publish(Event(
                    action="RISK_REBASELINED", agent="risk",
                    result=f"ตั้ง baseline จากบัญชีจริง: balance {acc.balance}",
                    payload={"balance": acc.balance}))
        except Exception:  # noqa: BLE001
            pass
    # Watch for the Windows bridge in case it was not up at import time; without
    # this the app would silently trade the simulator for its whole lifetime.
    mt5_supervisor.start(_on_mt5_recovered, interval=20.0)
    _rt_tasks.append(asyncio.create_task(market_tick_loop(connector, settings.symbols)))
    _rt_tasks.append(asyncio.create_task(account_snapshot_loop(connector)))
    _rt_tasks.append(asyncio.create_task(make_heartbeat_loop(lambda: loop, connector, settings)()))
    _rt_tasks.append(asyncio.create_task(_firestore_status_loop()))


async def _firestore_status_loop() -> None:
    """Publish agent/mt5 status to Firestore every 30s (cheap, not per-tick)."""
    import os
    runner = os.environ.get("RUNNER_NAME", "local")
    while True:
        try:
            if settings.firestore_enabled:
                positions = []
                account = {}
                try:
                    positions = [p.__dict__ for p in await connector.get_positions()]
                    acc = await connector.get_account_status()
                    account = {"balance": acc.balance, "equity": acc.equity,
                               "currency": acc.currency, "server": acc.server, "mode": acc.mode}
                except Exception:  # noqa: BLE001 - keep status flowing even if MT5 hiccups
                    pass
                mirror.push_system_status({
                    "runner": runner,
                    "agent": loop.status,
                    "trading_mode": settings.trading_mode,
                    "live_trading": settings.live_trading,
                    "symbols": settings.symbols,
                    "positions": positions,
                    "account": account,
                })
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(30)


@app.on_event("shutdown")
async def shutdown() -> None:
    await loop.stop()
    for t in _rt_tasks:
        t.cancel()


# ---------------- REST ----------------
@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "mode": settings.trading_mode, "ts": loop.status["last_beat"]}


@app.get("/api/system/status")
async def system_status(user: dict = Depends(require_user)) -> dict:
    mt5 = await connector.get_status()
    # REAL_MT5_CONNECTED and SIMULATOR_CONNECTED are separate facts. `connected`
    # alone must never be read as "MT5 is connected" — the VPS was reporting
    # connected=true while running on the simulator.
    real = connector_source == "REAL_MT5" and bool(mt5.get("connected"))
    sim = connector_source == "SIMULATOR"
    return {
        "agent": loop.status,
        "mt5": {**mt5, "data_source": connector_source,
                "REAL_MT5_CONNECTED": real, "SIMULATOR_CONNECTED": sim},
        "mcp": {"mode": settings.mt5_mode, "allowed_tools": True,
                "server_host": settings.mt5_server_host,
                "server_port": settings.mt5_server_port,
                "inprocess": settings.mt5_bridge_inprocess},
        "trading_mode": settings.trading_mode,
        "live_trading": settings.live_trading,
        "symbols": settings.symbols,
    }


@app.get("/api/monitor")
async def monitor(symbol: str | None = None, user: dict = Depends(require_user)) -> dict:
    """Single read-only snapshot for the command platform (balance, price, day
    direction, live indicators, signals, stats). Never trades."""
    from app.core.monitor import build_monitor_snapshot
    return await build_monitor_snapshot(connector, source=mt5_supervisor.source_now(),
                                        loop_status=loop.status, symbol=symbol, db=db)


@app.get("/api/chat/context")
async def chat_context(user: dict = Depends(require_user)) -> dict:
    """Live context block the panel shows above the chat so both sides always share
    the same picture: account, price, day direction, indicators, signals, stats."""
    from app.core.monitor import build_monitor_snapshot
    snap = await build_monitor_snapshot(connector, source=mt5_supervisor.source_now(),
                                        loop_status=loop.status, db=db)
    return {"ts": snap["ts"], "account": snap["account"], "price": snap["price"],
            "day": snap["day"], "signals": snap["signals"],
            "indicators": {"stochastic": snap["indicators"].get("stochastic"),
                           "curve": snap["indicators"].get("curve"),
                           "timeframe": snap["indicators"].get("timeframe"),
                           "supporting": snap["indicators"].get("supporting_indicators")},
            "positions": snap["positions"], "stats": snap["stats"],
            "agent": snap["agent"], "trading": snap["trading"],
            "data_source": snap["data_source"], "symbol": snap["symbol"]}


@app.get("/api/models")
async def models_list(user: dict = Depends(require_user)) -> dict:
    """Available OpenRouter models + which category is pinned to which."""
    from app.ai.model_router import CATEGORIES, router
    active = {c: router.model_for(c) for c in CATEGORIES}
    available: list[dict] = []
    if settings.llm_api_key:
        try:
            req = urllib.request.Request(settings.llm_base_url.rstrip("/") + "/models",
                                         headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            available = [{"id": m.get("id"), "name": m.get("name")} for m in data.get("data", [])][:400]
        except Exception as e:  # noqa: BLE001
            return {"configured": True, "error": str(e), "active": active, "available": []}
    return {"configured": bool(settings.llm_api_key), "base_url": settings.llm_base_url,
            "active": active, "available": available, "categories": CATEGORIES}


@app.post("/api/models/select")
async def models_select(body: dict, user: dict = Depends(require_user)) -> dict:
    """Pin a task category to an OpenRouter model (or clear it with empty string).
    Persisted in the DB so it survives restarts and needs no container rebuild."""
    from app.ai.model_router import CATEGORIES
    category = str(body.get("category", ""))
    raw = body.get("model", "")
    model = "" if raw is None else str(raw).strip()
    if category not in CATEGORIES:
        raise HTTPException(400, f"unknown category: {category}")
    if not settings.llm_api_key:
        raise HTTPException(400, "LLM_API_KEY not configured")
    routes = db.kv_get("model_routes", {}) or {}
    if model:
        routes[category] = model
    else:
        routes.pop(category, None)
    db.kv_set("model_routes", routes)
    from app.ai.model_router import router
    router._cache.pop(category, None)  # force re-read from the persisted route
    await bus.publish(Event(action="MODEL_ROUTE_CHANGED", result=f"{category} → {model or 'default'}",
                            user=user.get("email", "user"), agent="platform"))
    return {"ok": True, "category": category, "model": router.model_for(category), "routes": routes}


@app.get("/api/market/{symbol}")
async def market(symbol: str, user: dict = Depends(require_user)) -> dict:
    return await MarketAgent(connector).snapshot(symbol.upper())


@app.get("/api/account")
async def account(user: dict = Depends(require_user)) -> dict:
    acc = await connector.get_account_status()
    positions = await connector.get_positions()
    return {"account": acc.__dict__, "positions": [p.__dict__ for p in positions]}


@app.get("/api/trades")
async def trades(limit: int = 100, user: dict = Depends(require_user)) -> dict:
    return {"trades": journal.recent(limit), "open": journal.open_trades()}


@app.get("/api/statistics")
async def statistics(version: str | None = None, user: dict = Depends(require_user)) -> dict:
    return {"summary": stats.summary(version), "today": stats.today()}


@app.get("/api/strategies")
async def strategies(user: dict = Depends(require_user)) -> dict:
    return {"strategies": registry.list(), "active": registry.active_version(),
            "proposals": registry.list_proposals()}


@app.get("/api/audit")
async def audit_query(limit: int = 100, action: str | None = None, user: dict = Depends(require_user)) -> dict:
    return {"events": audit.query(limit=limit, action=action)}


@app.get("/api/chat/history")
async def chat_history(limit: int = 100, user: dict = Depends(require_user)) -> dict:
    return {"messages": list(reversed(chat.history(limit)))}


@app.post("/api/chat")
async def chat_send(body: dict, user: dict = Depends(require_user)) -> dict:
    text = (body or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    out = await chat.handle(text, user=user.get("email") or user.get("uid") or "user")
    await manager.broadcast({"type": "CHAT_MESSAGE", "payload": out})
    return out


@app.post("/api/backtest/{version}")
async def run_backtest(version: str, user: dict = Depends(require_user)) -> dict:
    try:
        result = await backtester.run(version)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, timeframe: str = "M15", count: int = 200,
                  user: dict = Depends(require_user)) -> dict:
    """Candles + indicator series derived from the ACTIVE strategy rules (not hard-coded)."""
    symbol = symbol.upper()
    market = await MarketAgent(connector).snapshot(symbol, timeframe=timeframe, count=max(count, 120))
    full = await connector.get_candles(symbol, timeframe, max(count + 60, 260))
    series: dict[str, list[float | None]] = {}
    closes = [c.close for c in full]
    try:
        rules = registry.rules(registry.active_version() or settings.default_strategy_id)
    except Exception:  # noqa: BLE001
        rules = []
    for rule in rules:
        rtype = rule.get("type")
        params = rule.get("params", {})
        try:
            if rtype == "EMA_CROSS":
                from app.mt5.simulator import ema
                for label, period in ((f"EMA{params.get('fast', 9)}", int(params.get('fast', 9))),
                                      (f"EMA{params.get('slow', 21)}", int(params.get('slow', 21)))):
                    vals = ema(closes, period)
                    series[label] = [None] * (len(full) - len(vals)) + vals
            elif rtype == "RSI_FILTER":
                from app.mt5.simulator import rsi
                vals = rsi(closes, int(params.get("period", 14)))
                series[f"RSI{params.get('period', 14)}"] = [None] * (len(full) - len(vals)) + vals
            elif rtype == "ATR_STOP":
                from app.mt5.simulator import atr
                vals = atr(full, int(params.get("period", 14)))
                series[f"ATR{params.get('period', 14)}"] = [None] * (len(full) - len(vals)) + vals
        except Exception:  # noqa: BLE001
            continue
    n = len(full)
    return {
        "symbol": symbol, "timeframe": timeframe,
        "candles": [asdict_c(c) for c in full[-count:]],
        "indicators": {k: (v[-count:] if v is not None else None) for k, v in series.items()},
        "current_price": market["price"],
        "indicators_now": market["indicators"],
    }


def asdict_c(c):
    return {"time": c.time, "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "tick_volume": c.tick_volume}


@app.get("/api/decisions")
async def decisions_list(limit: int = 50, user: dict = Depends(require_user)) -> dict:
    return {"decisions": db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))}


@app.get("/api/v1/state")
async def v1_state(user: dict = Depends(require_user)) -> dict:
    """Strategy V1 realtime state: stochastic, curve, chart context, last decision.
    Powers the dashboard's STOCHASTIC panel and 'Why didn't it trade?' (spec §26–27)."""
    import json as _json
    symbol = settings.symbols[0] if settings.symbols else "GOLD"
    try:
        out = await decision_agent.decide(symbol, registry.active_version() or settings.default_strategy_id,
                                          timeframe=settings.strategy_timeframe)
        st = out.market_state.get("stochastic_state")
    except Exception as e:  # noqa: BLE001
        st = None
        out = None
        err = str(e)[:200]
    else:
        err = ""
    last = db.query_one("SELECT * FROM decisions ORDER BY id DESC LIMIT 1")
    detail = {}
    if last and last["detail_json"]:
        try:
            detail = _json.loads(last["detail_json"])
        except _json.JSONDecodeError:
            detail = {}
    # Price Action snapshot (PRICE_ACTION_CANDLE_CONTEXT_V1): deterministic, cheap.
    # Real evaluations happen on curve turns; between signals this is a clearly-marked
    # probe so the dashboard can show the knowledge layer working (spec §36 auditability).
    snapshot = None
    try:
        from app.core.price_action import build_context
        curve_now = (st.curve if st else None)
        side = "BUY" if (curve_now and curve_now.name == "TURNING_UP") else "SELL"
        candles = await connector.get_candles(symbol, settings.strategy_timeframe, 120)
        probe = loop._candles_stale(candles, settings.strategy_timeframe)
        pa = build_context(candles, side, stale=probe)
        snapshot = pa.to_dict()
        snapshot["probe_side"] = side
        snapshot["probe"] = curve_now is None or curve_now.name not in ("TURNING_UP", "TURNING_DOWN")
    except Exception:  # noqa: BLE001
        snapshot = None
    return {
        "strategy_id": "STRATEGY_V1_STOCHASTIC_CURVE",
        "strategy_version": "1.0.0",
        "timeframe": settings.strategy_timeframe,
        "autonomous": settings.autonomous_mode,
        "stochastic": st.to_dict() if st else (detail.get("stochastic") or {}),
        "chart_context": detail.get("chart_context") or {},
        "price_action_snapshot": snapshot,
        "last_decision": {
            "ts": last["ts"], "symbol": last["symbol"], "decision": last["decision"],
            "risk_result": last["risk_result"], "reason": last["reason"],
            "detail": detail,
        } if last else None,
        "error": err,
    }


@app.post("/api/control/close-all")
async def close_all(body: dict, user: dict = Depends(require_user)) -> dict:
    """Supervisor CLOSE ALL (spec §30): close managed positions after explicit confirm."""
    if not body.get("confirm"):
        raise HTTPException(status_code=400, detail="confirm required")
    positions = await connector.get_positions()
    closed, failed = [], []
    for p in positions:
        result = await connector.close_position(p.ticket)
        if result.ok:
            closed.append({"ticket": p.ticket, "symbol": p.symbol})
        else:
            failed.append({"ticket": p.ticket, "error": result.error})
    await bus.publish(Event(action="SUPERVISOR_CLOSE_ALL",
                            result=f"closed={len(closed)} failed={len(failed)}",
                            user=user.get("email") or "user", agent="dashboard",
                            payload={"closed": closed, "failed": failed}))
    return {"ok": not failed, "closed": closed, "failed": failed}


# ---------------- MT5 provider (broker) switching ----------------
def _switch_connector(new_connector) -> None:
    """Runtime-safe connector swap: loop/risk/API read module-global `connector`.
    Strategy uses canonical symbols; the connector does broker mapping internally."""
    global connector
    old = connector
    connector = new_connector
    try:
        from app.core.agents import DecisionAgent, StrategyAgent
        from app.core.strategy_engine import StrategyEngine
        decision_agent = DecisionAgent(new_connector,
                                       StrategyAgent(StrategyEngine(registry), registry), registry)
        loop.bind_agents(decision_agent)
    except Exception:  # noqa: BLE001 - binding must never strand the loop
        pass
    # old stdio bridge process terminates with the object; nothing to join explicitly
    del old


async def _on_mt5_recovered(client, source: str) -> None:
    """Called by the supervisor when the real bridge finally answers.

    Swaps the global connector (and rebinds the decision agent, via
    _switch_connector) so the loop starts trading real MT5 data, and records the
    upgrade so the platform reports REAL_MT5 rather than the startup snapshot.
    """
    _switch_connector(client)
    mt5_supervisor.mark(client, source)
    # Rebase the loss limits onto the account we are actually trading. Without this
    # the gates keep comparing against the simulator's 10,000 equity and report a
    # ~99% drawdown on a real 52-USD account, blocking every order.
    try:
        acc = await client.get_account_status()
        if acc and acc.balance > 0:
            loop.risk_gate.rebase(acc.balance)
    except Exception:  # noqa: BLE001 - never fail the upgrade over a baseline
        pass
    await bus.publish(Event(
        action="MT5_RECONNECTED", result=f"{connector_source} -> {source}",
        user="system", agent="reconnect"))
    notifier.send("✅ <b>MT5 bridge กลับมาแล้ว</b>\n"
                  "เชื่อมต่อ MT5 จริงสำเร็จ — เลิกใช้ข้อมูล simulator")


@app.get("/api/providers")
async def providers_list(user: dict = Depends(require_user)) -> dict:
    from app.mt5 import providers as prov_mod
    provs = prov_mod.load_providers()
    return {
        "active": prov_mod.get_active_provider_id(),
        "providers": [p.to_dict() for p in provs.values()],
        "mt5_mode": settings.mt5_mode,
    }


@app.post("/api/providers/select")
async def providers_select(body: dict, user: dict = Depends(require_user)) -> dict:
    """Switch broker provider at runtime (symbol mapping only — MT5 login stays
    in the terminal; this never touches credentials)."""
    from app.mt5 import providers as prov_mod
    pid = (body or {}).get("provider", "").strip().lower()
    try:
        prov = prov_mod.set_active_provider(pid)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # simulator selection → swap connector to SimulatorConnector (clearly SIMULATED)
    if pid == "simulator" and settings.mt5_mode != "SIMULATOR_ONLY_LOCK":
        from app.mt5.simulator import SimulatorConnector
        _switch_connector(SimulatorConnector(symbols=settings.symbols))
    else:
        # (re)build MCP client so any new mapping takes effect + verify it answers
        from app.mt5.mcp_client import MT5MCPClient
        client = MT5MCPClient()
        try:
            client.ensure_initialized()
            _switch_connector(client)
        except Exception as e:  # noqa: BLE001
            await bus.publish(Event(action="PROVIDER_SWITCH", result=f"MCP init failed: {str(e)[:120]}",
                                    user=user.get("email") or "user", agent="dashboard"))
            raise HTTPException(status_code=502, detail=f"MT5 bridge ไม่ตอบสนอง: {str(e)[:200]}")
    status = await new_connector_status()
    await bus.publish(Event(action="PROVIDER_SWITCH", result=f"→ {prov.name} ({prov.id}) mt5={status.get('connected')}",
                            user=user.get("email") or "user", agent="dashboard",
                            payload={"provider": prov.id, **status}))
    return {"ok": True, "active": prov.id, "provider": prov.to_dict(), "mt5": status}


async def new_connector_status() -> dict:
    try:
        return await connector.get_status()
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "mode": settings.mt5_mode, "error": str(e)[:200]}


@app.post("/api/mt5/reconnect")
async def mt5_reconnect(user: dict = Depends(require_user)) -> dict:
    """Rebuild the MCP bridge and verify the terminal answers (system.get_status)."""
    from app.mt5.mcp_client import MT5MCPClient
    client = MT5MCPClient()
    try:
        client.ensure_initialized()
    except Exception as e:  # noqa: BLE001
        await bus.publish(Event(action="MT5_RECONNECT", result=f"FAILED: {str(e)[:120]}",
                                user=user.get("email") or "user", agent="dashboard"))
        return {"ok": False, "error": str(e)[:300]}
    _switch_connector(client)
    status = await new_connector_status()
    await bus.publish(Event(action="MT5_RECONNECT",
                            result=f"connected={status.get('connected')}",
                            user=user.get("email") or "user", agent="dashboard", payload=status))
    return {"ok": bool(status.get("connected")), "mt5": status}


# ---------------- Platform login (password + Google Authenticator) ----------------
@app.get("/api/auth/status")
async def auth_status() -> dict:
    return {
        "enabled": settings.platform_auth_enabled,
        "enrolled": platform_auth.is_enrolled(settings.platform_admin_email) if platform_auth else False,
        "user": settings.platform_admin_email if settings.platform_auth_enabled else None,
    }


@app.post("/api/auth/enroll")
async def auth_enroll(request: Request, body: dict) -> dict:
    """Start TOTP enrolment. Returns the otpauth URI — shown once, never again.

    Closed once an account is enrolled, so this cannot be used to silently take
    over the console. Re-enrolment requires a valid session for the same account
    (see the identity check below) or a matching PLATFORM_SETUP_TOKEN.
    """
    email = (body or {}).get("email", "").strip().lower()
    password = (body or {}).get("password", "")
    token = (body or {}).get("setup_token", "")
    if not email or len(password) < 10:
        raise HTTPException(status_code=400, detail="ต้องมี email และรหัสผ่านอย่างน้อย 10 ตัว")
    if email != settings.platform_admin_email.lower():
        raise HTTPException(status_code=403, detail="อีเมลไม่ตรงกับบัญชีที่อนุญาต")

    if platform_auth.any_enrolled_user():
        # Already set up: only the signed-in owner (or an operator holding the
        # setup token) may re-enrol. Without this, /api/auth/enroll would be an
        # unauthenticated password-reset endpoint.
        ident = platform_auth.user_for_token(request.cookies.get(SESSION_COOKIE))
        owned = ident is not None and ident.email == email
        has_token = bool(settings.platform_setup_token) and \
            hmac.compare_digest(token or "", settings.platform_setup_token)
        if not (owned or has_token):
            raise HTTPException(
                status_code=403,
                detail="ตั้งค่าแล้ว — เข้าสู่ระบบด้วยบัญชีเดิม หรือใช้ setup token เพื่อรีเซ็ต")

    platform_auth.create_user(email, password)
    secret = platform_auth.begin_totp_enrolment(email)
    return {"ok": True, "email": email, "secret": secret,
            "otpauth_uri": otpauth_uri(secret, email)}


@app.post("/api/auth/enroll/confirm")
async def auth_enroll_confirm(body: dict) -> dict:
    email = (body or {}).get("email", "").strip().lower()
    code = (body or {}).get("code", "")
    platform_auth.confirm_totp_enrolment(email, code)
    return {"ok": True, "enrolled": True}


@app.post("/api/auth/login")
async def auth_login(response: Response, body: dict) -> dict:
    email = (body or {}).get("email", "").strip().lower()
    token, ident = platform_auth.login(email, (body or {}).get("password", ""),
                                       (body or {}).get("code", ""))
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.session_ttl_days * 86400,
        httponly=True, samesite="lax",
        # The platform is reached over plain HTTP on a Tailscale address, so
        # Secure would silently drop the cookie. Set SECURE_COOKIES=true once it
        # is served over HTTPS.
        secure=settings.secure_cookies)
    await bus.publish(Event(action="AUTH_LOGIN", result=email, user=email, agent="platform"))
    return {"ok": True, "email": ident.email}


@app.post("/api/auth/logout")
async def auth_logout(response: Response, request: Request) -> dict:
    platform_auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(require_user)) -> dict:
    return {"email": user.get("email"), "uid": user.get("uid")}


# ---------------- Telegram ----------------
@app.get("/api/telegram/status")
async def telegram_status(user: dict = Depends(require_user)) -> dict:
    return {
        "configured": notifier.configured,
        "chat_id_set": bool(settings.telegram_chat_id),
        "token_set": bool(settings.telegram_bot_token),
    }


@app.post("/api/telegram/test")
async def telegram_test(body: dict, user: dict = Depends(require_user)) -> dict:
    """Send a real test message. Accepts optional one-shot token/chat_id in the
    request body (never persisted — use for first-time setup from the dashboard).
    Empty body = test the configured env values."""
    token = (body or {}).get("token") or settings.telegram_bot_token
    chat_id = (body or {}).get("chat_id") or settings.telegram_chat_id
    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="token/chat_id ยังไม่ตั้งค่า (ดู infra/telegram/README-TELEGRAM-TH.md)")
    text = (body or {}).get("text") or (f"✅ AI_MT5 test\n[{settings.trading_mode}][{'AUTONOMOUS' if settings.autonomous_mode else 'MANUAL'}]\n"
                                        f"strategy {registry.active_version()} · loop {'running' if loop.status['running'] else 'stopped'}")
    tmp = TelegramNotifier(token=token, chat_id=chat_id)
    ok = tmp.send(text)
    await bus.publish(Event(action="TELEGRAM_TEST", result=f"ok={ok}",
                            user=user.get("email") or "user", agent="dashboard"))
    if not ok:
        raise HTTPException(status_code=502, detail="ส่งไม่สำเร็จ — เช็ค token/chat_id และว่าได้กด Start กับบอทแล้ว")
    return {"ok": True}


@app.get("/api/trade/{trade_id}/detail")
async def trade_detail(trade_id: int, user: dict = Depends(require_user)) -> dict:
    """Full traceability for a trade marker click: decision, risk, audit chain."""
    tr = db.query_one("SELECT * FROM trades WHERE id=?", (trade_id,))
    if not tr:
        raise HTTPException(status_code=404, detail="trade not found")
    decision = db.query_one(
        "SELECT * FROM decisions WHERE symbol=? AND strategy_version=? AND decision IN ('BUY','SELL') "
        "AND id <= (SELECT COALESCE(MAX(id),0) FROM decisions) ORDER BY id DESC LIMIT 1", (tr["symbol"], tr["strategy_version"]))
    opens = db.query("SELECT * FROM audit_log WHERE action='TRADE_OPEN' ORDER BY id DESC LIMIT 20")
    open_ev = next((o for o in opens if tr["ticket"] and tr["ticket"] in (o.get("payload_json") or "")), None)
    closes = db.query("SELECT * FROM audit_log WHERE action='TRADE_CLOSE' ORDER BY id DESC LIMIT 30")
    close_ev = next((o for o in closes if tr["ticket"] and tr["ticket"] in (o.get("result") or "")), None)
    return {"trade": tr, "decision": decision, "open_event": open_ev, "close_event": close_ev}


@app.get("/api/analytics/equity-curve")
async def equity_curve(days: int = 30, user: dict = Depends(require_user)) -> dict:
    rows = db.query(
        "SELECT ts_close, pnl FROM trades WHERE pnl IS NOT NULL AND ts_close IS NOT NULL ORDER BY id")
    acc = await connector.get_account_status()
    equity = acc.balance - sum(r["pnl"] for r in rows)
    points = []
    peak = equity
    max_dd = 0.0
    for r in rows:
        equity += r["pnl"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        points.append({"ts": r["ts_close"], "equity": round(equity, 2), "pnl": r["pnl"],
                       "drawdown": round(equity - peak, 2)})
    return {"start": round(acc.balance - sum(r["pnl"] for r in rows), 2), "points": points,
            "max_drawdown": round(max_dd, 2), "current_equity": acc.equity}


@app.get("/api/learning/summary")
async def learning_summary(user: dict = Depends(require_user)) -> dict:
    s = stats.summary()
    per = db.query("SELECT * FROM strategy_performance ORDER BY pnl DESC")
    losing = journal.losing_trades(10)
    return {"summary": s, "per_strategy": per, "recent_losers": losing,
            "active": registry.active_version()}


@app.post("/api/learning/review")
async def learning_review(user: dict = Depends(require_user)) -> dict:
    from app.core.learning import LearningEngine
    eng = LearningEngine(db, registry, journal, stats)
    return await eng.review_and_propose(created_by=user.get("email") or "dashboard")


@app.post("/api/control/pause")
async def pause(body: dict, user: dict = Depends(require_user)) -> dict:
    db.kv_set("paused", bool(body.get("paused", True)))
    action = "TRADING_PAUSED" if body.get("paused", True) else "TRADING_RESUMED"
    await bus.publish(Event(action=action, user=user.get("email") or "user", agent="dashboard"))
    return {"ok": True, "paused": body.get("paused", True)}


@app.post("/api/control/emergency-stop")
async def emergency(body: dict, user: dict = Depends(require_user)) -> dict:
    on = bool(body.get("on", True))
    db.kv_set("emergency_stop", on)
    await bus.publish(Event(action="EMERGENCY_STOP", result="on" if on else "off",
                            user=user.get("email") or "user", agent="dashboard"))
    return {"ok": True, "emergency_stop": on}


@app.post("/api/strategies/{version}/activate")
async def activate_strategy(version: str, user: dict = Depends(require_user)) -> dict:
    try:
        registry.set_active_version(version)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    await bus.publish(Event(action="STRATEGY_ACTIVATED", result=version, user=user.get("email") or "user"))
    return {"ok": True, "active": version}


# ---------------- WebSocket ----------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            # client may send pings; we mostly push
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong", "ts": loop.status["last_beat"]})
    except WebSocketDisconnect:
        manager.disconnect(ws)
