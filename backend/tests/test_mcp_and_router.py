import pytest

from app.ai.model_router import ModelRouter
from app.config import settings
from app.mt5.mcp_client import MT5MCPClient


def test_mcp_tool_whitelist_blocks_shell():
    c = MT5MCPClient()
    with pytest.raises(PermissionError):
        c.call_tool("shell.exec", {"cmd": "rm -rf /"})
    with pytest.raises(PermissionError):
        c.call_tool("filesystem.read", {"path": "/etc/passwd"})
    with pytest.raises(PermissionError):
        c.call_tool("os.execute", {"cmd": "whoami"})


def test_mcp_allowed_tools_exist():
    for tool in ("market.get_price", "market.get_candles", "market.get_indicator", "market.get_symbol_info",
                 "account.get_status", "account.get_positions", "account.get_history",
                 "trade.open", "trade.close", "trade.modify", "system.get_status"):
        assert tool in MT5MCPClient.__init__.__globals__["ALLOWED_TOOLS"]


def test_router_categories(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    r = ModelRouter()
    for cat in ("market_classification", "strategy_evaluation", "trade_decision", "trade_analysis",
                "statistics", "research", "backtesting_analysis", "strategy_generation", "coding", "chat"):
        assert r.model_for(cat)
    with pytest.raises(ValueError):
        r.model_for("not_a_category")


def test_router_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "model_routes", {})
    r = ModelRouter()
    assert r.model_for("research") is None


def test_router_env_override(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {"chat": "vendor/my-model-x"})
    r = ModelRouter()
    assert r.model_for("chat") == "vendor/my-model-x"
