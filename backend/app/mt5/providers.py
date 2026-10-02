"""MT5 broker provider registry.

เลือกโบรกเกอร์ (provider) สำหรับ MT5 ได้จาก dashboard หรือ env — แต่ละ provider
มี preset: ชื่อ, symbol mapping (XM ใช้ GOLD, broker ส่วนใหญ่ใช้ XAUUSD), server hint
หน้าที่: 1) map symbol canonical (XAUUSD) → ชื่อที่โบรกเกอร์ใช้จริง
        2) แสดงผลใน dashboard  3) ช่วย debug การเชื่อมต่อ
หมายเหตุ: การ login account ยังเกิดใน MT5 terminal (terminal ล็อกอินอยู่ที่ไหน
connector ต่อที่นั่น) — โมดูลนี้จึงเป็น selection/mapping เท่านั้น ไม่ยุ่ง credentials
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class Provider:
    id: str
    name: str
    symbol_map: dict[str, str]          # canonical (XAUUSD) → broker symbol
    notes: str = ""
    broker_server_hint: str = ""
    login_url: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["builtin"] = self.id != "custom"
        return d

    def map_symbol(self, canonical: str) -> str:
        return self.symbol_map.get(canonical.upper(), canonical.upper())


def _p(pid: str, name: str, symbol_map: dict[str, str], notes: str = "",
       server: str = "", url: str = "") -> Provider:
    # กันพลาด: provider ใดต้องมี XAUUSD เสมอ (canonical หลักของระบบ)
    symbol_map.setdefault("XAUUSD", "XAUUSD")
    return Provider(id=pid, name=name, symbol_map=symbol_map, notes=notes,
                    broker_server_hint=server, login_url=url)


_BUILTIN: dict[str, Provider] = {
    "xm": _p(
        "xm", "XM Global",
        {"XAUUSD": "GOLD", "XAGUSD": "SILVER"},
        notes="XM ตั้งชื่อทองคำเป็น GOLD (ไม่มี XAUUSD) — ระบบ map ให้อัตโนมัติ",
        server="XMGlobal-MT5 2 (demo: XMGlobal-MT5 2 trial 2)",
        url="https://www.xm.com/",
    ),
    "exness": _p(
        "exness", "Exness",
        {"XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"},
        notes="Standard/Cent ใช้ suffix 'm' — Raw/Pro ไม่มี suffix (แก้ symbol map ได้)",
        server="Exness-MT5Trial8 / Exness-MT5Real8",
        url="https://www.exness.com/",
    ),
    "icmarkets": _p(
        "icmarkets", "IC Markets",
        {"XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD"},
        notes="Standard/Raw ใช้ชื่อตรงตัว",
        server="ICMarketsSC-MT5 / ICMarketsSC-MT5 (demo)",
        url="https://www.icmarkets.com/",
    ),
    "pepperstone": _p(
        "pepperstone", "Pepperstone",
        {"XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD"},
        notes="Standard/Razor ใช้ชื่อตรงตัว",
        server="Pepperstone-Demo / Pepperstone-Live",
        url="https://pepperstone.com/",
    ),
    "simulator": _p(
        "simulator", "Built-in Simulator",
        {"XAUUSD": "XAUUSD"},
        notes="ไม่ต่อ broker จริง — โหมดทดสอบ (SIMULATED); LIVE จะ fail-closed เสมอ",
    ),
}


def load_providers() -> dict[str, Provider]:
    """Builtin + custom providers จาก env:
    MT5_PROVIDERS='{"mybroker": {"name": "...", "symbol_map": {"XAUUSD": "GOLD.a"},
                                "notes": "...", "broker_server_hint": "..."}}'
    """
    provs: dict[str, Provider] = dict(_BUILTIN)
    custom = os.environ.get("MT5_PROVIDERS", "").strip()
    if custom:
        try:
            data = json.loads(custom)
            for pid, p in (data or {}).items():
                if not isinstance(p, dict):
                    continue
                smap = {str(k).upper(): str(v) for k, v in (p.get("symbol_map") or {}).items()}
                provs[str(pid)] = _p(str(pid), str(p.get("name", pid)), smap or {"XAUUSD": "XAUUSD"},
                                     notes=str(p.get("notes", "custom provider (env)")),
                                     server=str(p.get("broker_server_hint", "")),
                                     url=str(p.get("login_url", "")))
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass  # bad config → keep builtins only
    return provs


_active_provider_id: str = os.environ.get("MT5_PROVIDER", "xm").strip().lower() or "xm"


def get_active_provider_id() -> str:
    return _active_provider_id


def get_active_provider() -> Provider:
    provs = load_providers()
    return provs.get(_active_provider_id) or provs["xm"]


def set_active_provider(pid: str) -> Provider:
    global _active_provider_id
    provs = load_providers()
    pid = (pid or "").strip().lower()
    if pid not in provs:
        raise KeyError(f"unknown provider: {pid}")
    _active_provider_id = pid
    return provs[pid]


def symbol_for(canonical: str) -> str:
    """Canonical symbol → broker symbol ของ provider ปัจจุบัน (ใช้ตอนยิง tool)."""
    return get_active_provider().map_symbol(canonical)
