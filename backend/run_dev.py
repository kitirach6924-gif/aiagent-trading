"""Run the backend locally (trading loop + API).

Default: REAL MT5 Demo via the bundled MCP bridge (MT5 terminal must be running).
Set MT5_MODE=SIMULATOR for the simulated broker instead.
"""
import os

os.environ["TRADING_MODE"] = "DEMO"          # fail-closed safety defaults
os.environ["LIVE_TRADING"] = "false"
os.environ["MT5_MODE"] = "MCP"
os.environ["SYMBOLS"] = "XAUUSD"            # canonical; provider map แปลงเป็น GOLD บน XM อัตโนมัติ
os.environ["AUTH_DISABLED"] = "true"
os.environ["DB_PATH"] = "data/trading.db"

# Firebase AI_MT5 mirror (my-first-project-24042)
os.environ.setdefault("FIREBASE_PROJECT_ID", "my-first-project-24042")
os.environ.setdefault("FIRESTORE_ENABLED", "true")
os.environ.setdefault("FIRESTORE_DATABASE_ID", "ai-mt5-default")
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "ai-mt5-backend-key.json")

import uvicorn

if __name__ == "__main__":
    # Local dev: 127.0.0.1. On a VPS set API_HOST=0.0.0.0 (protected by firewall/NSG).
    uvicorn.run("app.main:app", host=os.environ.get("API_HOST", "127.0.0.1"),
                port=int(os.environ.get("API_PORT", "8000")), reload=False)
