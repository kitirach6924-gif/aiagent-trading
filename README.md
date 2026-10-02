# Autonomous AI Trading Agent Platform

MT5 (Demo) + MCP + Firebase Dashboard + AI Agent Chat + Telegram + Model Routing + Learning.

**Safety first: the system starts in DEMO-only, fail-closed mode. Real-money trading stays disabled until every readiness gate passes AND a human explicitly enables it.**

## Architecture

```
Firebase Web App (dashboard, auth, chat UI, monitoring)
        │  REST + WebSocket (Firebase ID token)
        ▼
Trading AI Core (FastAPI, runs 24/7 on the VPS)
  ├─ Market Agent (read-only)
  ├─ Strategy Engine (deterministic rule interpreter, versioned registry)
  ├─ Decision Agent (auditable BUY/SELL/WAIT summary)
  ├─ Risk Gate (deterministic code — no LLM can override)
  ├─ Trade Journal + Statistics + Audit Log (SQLite, agent cannot delete)
  ├─ Learning Engine (proposes only, never approves)
  ├─ Backtester (baseline vs proposal, factual metrics)
  └─ Model Router (OpenRouter-compatible, per-task categories)
        │  whitelisted MCP tools only
        ▼
MT5 MCP bridge ──► MetaTrader 5 ──► Demo account
Trading Agent ──► Telegram Bot (notifications)
```

- The **24/7 loop lives in the backend on your VPS** — never inside Firebase Hosting.
- Firebase hosts the **dashboard** (React + TS + Vite) and provides **Auth + Firestore**.
- Without an MT5 terminal (e.g. local dev / CI), the core runs against a built-in **simulator broker** — full pipeline, zero risk. In LIVE mode this fallback is forbidden (fail-closed).

## Safety model

Defaults (env-only, the AI has no API to change them):

```text
TRADING_MODE=DEMO          LIVE_TRADING=false
AUTO_STRATEGY_DEPLOY=false AUTO_RISK_CHANGE=false
```

- **Risk Gate** is plain deterministic Python: max lot, max risk %, max open positions, max daily loss, max drawdown, spread limit, SL required, session filter, consecutive losses, symbol/strategy permission, trading mode, emergency stop. Every BLOCK is logged.
- **LLM outputs are suggestions only.** Strategy changes enter the registry as PROPOSAL → (backtest/demo evidence) → human `approve` → APPROVED → human `activate`.
- Chat commands like "หยุดเปิดออเดอร์ใหม่" / "Resume Trading" / "emergency stop" work; risk limits and SL protection cannot be altered by chat.
- Audit log and trade journal are **append-only** for the agent (no delete API exists).

## Quick start (dev, no MT5 needed)

```bash
# Backend (port 8000) — simulator broker, auth disabled for local dev
cd backend
pip install -r requirements.txt
python run_dev.py

# Frontend (port 5173)
cd ../frontend
npm install
npm run dev
```

Open http://localhost:5173. With `AUTH_DISABLED=true` the backend accepts requests without a Firebase token; to use real Firebase Auth, fill `frontend/.env` (see `.env.example`) and set `FIREBASE_PROJECT_ID` in the backend env.

### Tests

```bash
cd backend
python -m pytest tests -q
```

Covers: risk gate (13 scenarios incl. fail-closed), strategy engine + registry validation, chat intent/proposal/pause/emergency flows, backtest determinism, MCP tool whitelist, model router, simulator trading cycle with journal + statistics.

## Connecting real MT5 (Demo) via MCP

1. Run an MT5 MCP bridge on the Windows machine with your MetaTrader 5 terminal (Demo account), e.g. any bridge exposing `market.get_price`, `market.get_candles`, `market.get_indicator`, `market.get_symbol_info`, `account.get_status`, `account.get_positions`, `account.get_history`, `trade.open`, `trade.close`, `trade.modify`, `system.get_status`.
2. Point the core at it:
   - `MT5_MODE=MCP` + `MT5_SERVER_HOST/PORT` (HTTP), or
   - `MT5_MCP_COMMAND="npx -y <bridge>"` (spawned over stdio).
3. The MCP client enforces a **tool whitelist** — shell/filesystem/OS tools are rejected by name before any call is made.

## Telegram

Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`. The agent pushes `TRADE_OPEN`, `TRADE_CLOSE`, `RISK_BLOCK`, `EMERGENCY_STOP`, pauses, approvals and backtest completions.

## AI Model Routing

Business logic calls `router.complete(category, ...)`. Categories: `market_classification`, `strategy_evaluation`, `trade_decision`, `trade_analysis`, `statistics`, `research`, `backtesting_analysis`, `strategy_generation`, `coding`, `chat`. Map each to any OpenRouter-compatible model via `MODEL_*` env vars. Cheap categories default to cheap models; heavy reasoning gets strong models. **No API key = deterministic fallbacks everywhere** (the platform runs fully without LLMs).

## Cost control in the loop

Tick → deterministic strategy filter → only when a setup appears: decision + risk gate + execution. The LLM is never called per-tick; analysis/learning calls use cheap models first.

## Chat-first strategy management (example)

```
You:    เพิ่มกฎว่า ถ้า spread สูงให้หยุดเทรด
Agent:  Proposed Rule: SPREAD_FILTER … Target: Strategy v1.1 … Status: PROPOSAL
You:    ทดสอบ v1.1
Agent:  BACKTEST_COMPLETED — Baseline v1.0 vs Proposal v1.1 (factual metrics, no auto-pick)
You:    อนุมัติ v1.1
Agent:  ✅ approved (human) → ใช้ v1.1 to activate
```

## VPS deployment

`infra/docker-compose.yml` runs the trading core with restart-always; dashboard deploy to Firebase Hosting (`firebase deploy --only hosting`). Keep `.env` out of git.

## What's intentionally NOT done (later phases)

- Live (real-money) mode — hard-disabled until all readiness gates pass + explicit human enablement (`LIVE_TRADING=true` is env-only).
- Firestore mirroring of the SQLite journal (`FIRESTORE_ENABLED`) — schema-ready, write path stubbed for the next phase.
- Walk-forward automation beyond single-seed backtests, and per-version demo validation periods.
