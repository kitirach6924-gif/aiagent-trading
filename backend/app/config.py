"""Central configuration. All values come from environment variables with fail-closed defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# backend/.env (optional) โหลดก่อนอ่าน env ทั้งหมด — env process ชนะไฟล์เสมอ
from app.integrations.env_loader import load_env_file as _load_env_file

_load_env_file()


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # ---- Safety / mode (fail-closed defaults) ----
    trading_mode: str = "DEMO"            # DEMO or LIVE (LIVE requires explicit env)
    live_trading: bool = False            # master kill-switch for real money
    auto_strategy_deploy: bool = False    # AI can never flip this; env only
    auto_risk_change: bool = False        # AI can never flip this; env only
    emergency_stop: bool = False          # runtime state, seeded from env

    # ---- Trading agent ----
    symbols: list[str] = field(default_factory=lambda: [
        s.strip() for s in os.environ.get("SYMBOLS", "XAUUSD").split(",") if s.strip()
    ])
    loop_interval_seconds: int = _int("LOOP_INTERVAL_SECONDS", 15)
    deterministic_filter_interval_seconds: int = _int("DET_FILTER_INTERVAL_SECONDS", 5)
    max_concurrent_loops: int = 1

    # ---- MT5 ----
    mt5_mode: str = "MCP"                 # MCP | SIMULATOR
    mt5_provider: str = os.environ.get("MT5_PROVIDER", "xm")  # broker provider id (see mt5/providers.py)
    mt5_mcp_command: str = ""             # optional external MCP server command
    # Env-readable: the Docker deployment points this at the Windows box over
    # Tailscale (e.g. 100.69.186.8). Previously hard-coded, which silently made
    # MT5_SERVER_HOST dead configuration.
    mt5_server_host: str = os.environ.get("MT5_SERVER_HOST", "127.0.0.1")
    mt5_server_port: int = _int("MT5_SERVER_PORT", 8765)
    mt5_bridge_inprocess: bool = _bool("MT5_BRIDGE_INPROCESS", True)  # spawn bundled stdio bridge
    # Bearer token for the HTTP bridge. Empty = no auth header sent (local stdio
    # path unaffected); the HTTP bridge itself refuses all traffic if IT has no token.
    mt5_bridge_auth_token: str = os.environ.get("MT5_BRIDGE_AUTH_TOKEN", "")
    mt5_bridge_timeout_seconds: float = _float("MT5_BRIDGE_TIMEOUT_SECONDS", 15.0)

    # ---- API ----
    api_host: str = "0.0.0.0"
    api_port: int = _int("API_PORT", 8000)
    api_cors_origins: list[str] = field(
        default_factory=lambda: [s.strip() for s in os.environ.get(
            "API_CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000,"
            "https://my-first-project-24042.web.app,https://my-first-project-24042.firebaseapp.com"
        ).split(",") if s.strip()]
    )
    firebase_project_id: str = os.environ.get("FIREBASE_PROJECT_ID", "")
    firebase_audiences: list[str] = field(default_factory=list)
    auth_disabled: bool = _bool("AUTH_DISABLED", False)  # local dev only
    # Platform login: PBKDF2 password + TOTP (Google Authenticator).
    session_ttl_days: int = int(os.environ.get("SESSION_TTL_DAYS", "7"))
    platform_auth_enabled: bool = _bool("PLATFORM_AUTH_ENABLED", False)
    platform_admin_email: str = os.environ.get("PLATFORM_ADMIN_EMAIL", "")
    secure_cookies: bool = _bool("SECURE_COOKIES", False)
    # One-shot operator credential that re-opens enrolment after setup. Optional:
    # without it, re-enrolment is only possible from an existing session.
    platform_setup_token: str = os.environ.get("PLATFORM_SETUP_TOKEN", "")

    # ---- Telegram ----
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    # ---- Telegram INBOUND bot (questions -> ChatAgent) ----
    # Allowlist. Falls back to TELEGRAM_CHAT_ID when empty, but an explicit list
    # is what you want once more than one chat exists.
    telegram_bot_allowed_chat_ids: str = os.environ.get("TELEGRAM_BOT_ALLOWED_CHAT_IDS", "")
    # Read-only is the default and should stay that way: the bot can read state
    # and explain, but CONTROL/STRATEGY intents are refused before they can flip
    # paused/emergency_stop or edit the strategy registry. Turning this off makes
    # a phone message able to stop trading.
    telegram_bot_read_only: bool = _bool("TELEGRAM_BOT_READ_ONLY", True)

    # ---- AI / Model router (OpenRouter-compatible) ----
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")
    llm_timeout_seconds: int = _int("LLM_TIMEOUT_SECONDS", 60)
    model_routes: dict[str, str] = field(default_factory=lambda: {
        "market_classification": os.environ.get("MODEL_MARKET_CLASSIFICATION", ""),
        "strategy_evaluation": os.environ.get("MODEL_STRATEGY_EVALUATION", ""),
        "trade_decision": os.environ.get("MODEL_TRADE_DECISION", ""),
        "trade_analysis": os.environ.get("MODEL_TRADE_ANALYSIS", ""),
        "statistics": os.environ.get("MODEL_STATISTICS", ""),
        "research": os.environ.get("MODEL_RESEARCH", ""),
        "backtesting_analysis": os.environ.get("MODEL_BACKTESTING_ANALYSIS", ""),
        "strategy_generation": os.environ.get("MODEL_STRATEGY_GENERATION", ""),
        "coding": os.environ.get("MODEL_CODING", ""),
        "chat": os.environ.get("MODEL_CHAT", ""),
        # The router knows 15 categories; these five had no env key at all, so
        # they silently fell back to the built-in default model no matter what
        # the operator configured.
        "chart_analysis": os.environ.get("MODEL_CHART_ANALYSIS", ""),
        "trade_reasoning": os.environ.get("MODEL_TRADE_REASONING", ""),
        "trade_journal": os.environ.get("MODEL_TRADE_JOURNAL", ""),
        "market_analysis": os.environ.get("MODEL_MARKET_ANALYSIS", ""),
        "strategy_analysis": os.environ.get("MODEL_STRATEGY_ANALYSIS", ""),
    })

    # ---- Risk limits (env-overridable, but NOT LLM-overridable) ----
    max_lot: float = _float("RISK_MAX_LOT", 0.10)
    # DEAD SETTING — read by nothing. The 1%-of-equity risk rule was removed on
    # purpose: under leverage an equity-relative risk percentage is not a
    # meaningful control, and position sizing is managed by the operator outside
    # this platform. Kept only so the env var is not mistaken for a live control;
    # changing it has NO effect. app/core/risk.py must not reference it.
    max_risk_percent: float = _float("RISK_MAX_RISK_PERCENT", 1.0)
    max_open_positions: int = _int("RISK_MAX_OPEN_POSITIONS", 3)
    max_daily_loss_percent: float = _float("RISK_MAX_DAILY_LOSS_PERCENT", 3.0)
    max_drawdown_percent: float = _float("RISK_MAX_DRAWDOWN_PERCENT", 10.0)
    max_spread_points: float = _float("RISK_MAX_SPREAD_POINTS", 50)
    stop_loss_required: bool = _bool("RISK_STOP_LOSS_REQUIRED", True)
    max_consecutive_losses: int = _int("RISK_MAX_CONSECUTIVE_LOSSES", 3)
    telegram_dedup_window_seconds: float = _float("RISK_DEDUP_WINDOW_SECONDS", 300.0)

    allowed_sessions: list[str] = field(
        default_factory=lambda: [s.strip() for s in os.environ.get(
            "RISK_ALLOWED_SESSIONS", "SYDNEY,TOKYO,LONDON,NEWYORK").split(",") if s.strip()]
    )

    # ---- Strategy defaults ----
    default_strategy_id: str = os.environ.get("DEFAULT_STRATEGY_ID", "STRATEGY_V1_STOCHASTIC_CURVE")
    strategy_confidence_threshold: float = _float("STRATEGY_CONFIDENCE_THRESHOLD", 0.55)

    # ---- Strategy V1: Stochastic Curve + Chart Context ----
    strategy_timeframe: str = os.environ.get("STRATEGY_TIMEFRAME", "M15")
    # Higher timeframe that must not oppose a NEW entry (user's rule 2026-10-01).
    # Exits are NOT gated by this — closing stays on the entry timeframe.
    # Set empty to disable.
    higher_tf_gate_timeframe: str = os.environ.get("HIGHER_TF_GATE_TIMEFRAME", "M15")
    # Self-tuning entry threshold (user's request 2026-10-01). The tuner is
    # bounded: it refuses to move on thin data and refuses any threshold that
    # would stop the book trading. Set false to pin the gate at 65.
    adaptive_threshold: bool = _bool("ADAPTIVE_THRESHOLD", True)
    stoch_k_period: int = _int("STOCH_K_PERIOD", 24)
    stoch_d_period: int = _int("STOCH_D_PERIOD", 24)
    stoch_slowing: int = _int("STOCH_SLOWING", 10)
    curve_min_slope_change: float = _float("CURVE_MIN_SLOPE_CHANGE", 0.5)
    curve_confirmation_bars: int = _int("CURVE_CONFIRMATION_BARS", 1)
    curve_smoothing: int = _int("CURVE_SMOOTHING", 1)
    chart_context_use_llm: bool = _bool("CHART_CONTEXT_USE_LLM", False)
    autonomous_mode: bool = _bool("AUTONOMOUS_MODE", True)
    # User's explicit choice: no hard stop loss, exits come from the opposite
    # Stochastic turn only. Risk is carried by the score gate plus the recovery
    # watchdog (which closes positions orphaned by an outage).
    autonomous_no_sl: bool = _bool("AUTONOMOUS_NO_SL", False)
    # Close any open position the agent no longer has a decision for (e.g. after a
    # bridge/VPS outage left it orphaned). Seconds between recovery checks.
    recovery_close_after: float = _float("RECOVERY_CLOSE_AFTER_SEC", 300.0)

    # ---- SQLite persistence (local, mirrored to Firestore by the agent) ----
    db_path: str = os.environ.get("DB_PATH", "data/trading.db")

    # ---- Firestore mirror (server key optional; dashboard reads Firestore directly) ----
    firestore_enabled: bool = _bool("FIRESTORE_ENABLED", False)
    firestore_database_id: str = os.environ.get("FIRESTORE_DATABASE_ID", "ai-mt5-default")
    firestore_status_doc_id: str = os.environ.get("FIRESTORE_STATUS_DOC_ID", "current")  # namespace per runner (local/cloudrun)
    google_application_credentials: str = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    # ---- Backtest ----
    backtest_start_equity: float = _float("BACKTEST_START_EQUITY", 10_000.0)


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
