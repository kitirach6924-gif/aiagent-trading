"""Provider registry tests: builtin presets, custom env providers, symbol mapping."""
from __future__ import annotations

import pytest

from app.mt5 import providers as prov


def test_builtin_providers_present():
    provs = prov.load_providers()
    for pid in ("xm", "exness", "icmarkets", "pepperstone", "simulator"):
        assert pid in provs
    assert provs["xm"].map_symbol("XAUUSD") == "GOLD"
    assert provs["exness"].map_symbol("XAUUSD") == "XAUUSDm"


def test_every_provider_has_canonical_gold():
    for pid, p in prov.load_providers().items():
        assert "XAUUSD" in p.symbol_map, f"{pid} missing canonical XAUUSD"


def test_default_active_is_xm():
    assert prov.get_active_provider_id() in ("xm", "exness", "icmarkets", "pepperstone", "simulator")


def test_switch_and_map_roundtrip(monkeypatch):
    monkeypatch.setattr(prov, "_active_provider_id", "xm")
    assert prov.symbol_for("XAUUSD") == "GOLD"
    prov.set_active_provider("exness")
    assert prov.symbol_for("XAUUSD") == "XAUUSDm"
    # reverse map back to canonical
    from app.mt5.mcp_client import MT5MCPClient
    assert MT5MCPClient._to_canonical("XAUUSDm") == "XAUUSD"
    prov.set_active_provider("xm")
    assert MT5MCPClient._to_canonical("GOLD") == "XAUUSD"


def test_unknown_provider_rejected():
    with pytest.raises(KeyError):
        prov.set_active_provider("no-such-broker")


def test_custom_provider_from_env(monkeypatch):
    import json
    monkeypatch.setenv("MT5_PROVIDERS", json.dumps({
        "mybroker": {"name": "My Broker", "symbol_map": {"XAUUSD": "GOLD.a"},
                     "notes": "test", "broker_server_hint": "MyBroker-Demo"}}))
    provs = prov.load_providers()
    assert "mybroker" in provs
    assert provs["mybroker"].map_symbol("XAUUSD") == "GOLD.a"
    monkeypatch.setattr(prov, "_active_provider_id", "mybroker")
    assert prov.symbol_for("XAUUSD") == "GOLD.a"
