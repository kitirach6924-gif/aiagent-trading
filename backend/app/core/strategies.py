"""Strategy Registry: versioned strategy definitions with approval workflow.

Rules are deterministic JSON (interpreted by Strategy Engine). Only validated
rule sets enter this registry — chat text alone can never become a rule.
"""
from __future__ import annotations

import json
import re
from app.core.db import Database
from app.core.events import iso_utc

VALID_STATUSES = ("PROPOSAL", "TESTING", "VALIDATED", "APPROVED", "PRODUCTION", "REJECTED", "ARCHIVED")


class StrategyError(Exception):
    pass


class StrategyRegistry:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- validation -------------------------------------------------
    RULE_TYPES = {"EMA_CROSS", "RSI_FILTER", "ATR_STOP", "SPREAD_FILTER", "SESSION_FILTER", "RANGE_BREAKOUT", "STOCHASTIC_CURVE"}

    def validate_rules(self, rules: list[dict]) -> None:
        if not isinstance(rules, list) or not rules:
            raise StrategyError("rules must be a non-empty list")
        for r in rules:
            if not isinstance(r, dict) or r.get("type") not in self.RULE_TYPES:
                raise StrategyError(f"unknown rule type in {r!r}")
            if not isinstance(r.get("params", {}), dict):
                raise StrategyError(f"params must be an object in {r!r}")

    # ---- CRUD --------------------------------------------------------
    def create(self, version: str, name: str, rules: list[dict], *,
               base_version: str | None = None, created_by: str = "system",
               status: str = "PROPOSAL", origin: str = "system") -> dict:
        self.validate_rules(rules)
        if self.get(version):
            raise StrategyError(f"strategy version {version} already exists")
        self.db.execute(
            "INSERT INTO strategies (version, base_version, name, status, rules_json, params_json, created_by, created_at, origin) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (version, base_version, name, status, json.dumps(rules, ensure_ascii=False), "{}",
             created_by, iso_utc(), origin),
        )
        return self.get(version) or {}

    def get(self, version: str) -> dict | None:
        return self.db.query_one("SELECT * FROM strategies WHERE version=?", (version,))

    def rules(self, version: str) -> list[dict]:
        row = self.get(version)
        if row:
            return json.loads(row["rules_json"])
        # fall back to a pending proposal (e.g. created via chat) so it can be backtested
        p = self.db.query_one(
            "SELECT rules_json FROM proposals WHERE proposed_version=? ORDER BY id DESC", (version,))
        if p:
            return json.loads(p["rules_json"])
        raise StrategyError(f"strategy {version} not found")

    def status(self, version: str) -> str:
        row = self.get(version)
        return row["status"] if row else "MISSING"

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM strategies ORDER BY id")

    def set_status(self, version: str, status: str, approved_by: str | None = None) -> None:
        if status not in VALID_STATUSES:
            raise StrategyError(f"invalid status {status}")
        if status in ("APPROVED", "PRODUCTION") and approved_by is None:
            raise StrategyError("human approval (approved_by) is required for APPROVED/PRODUCTION")
        self.db.execute(
            "UPDATE strategies SET status=?, approved_by=COALESCE(?, approved_by), approved_at=CASE WHEN ? IN ('APPROVED','PRODUCTION') THEN ? ELSE approved_at END "
            "WHERE version=?",
            (status, approved_by, status, iso_utc(), version),
        )

    def production_version(self) -> str | None:
        row = self.db.query_one("SELECT version FROM strategies WHERE status='PRODUCTION' ORDER BY approved_at DESC LIMIT 1")
        return row["version"] if row else None

    def active_version(self) -> str:
        row = self.db.query_one(
            "SELECT version FROM strategies WHERE status IN ('PRODUCTION','APPROVED','VALIDATED') "
            "ORDER BY CASE status WHEN 'PRODUCTION' THEN 0 WHEN 'APPROVED' THEN 1 ELSE 2 END, approved_at DESC LIMIT 1"
        )
        if row:
            return row["version"]
        return self.db.kv_get("active_strategy_version", "")

    def set_active_version(self, version: str) -> None:
        row = self.get(version)
        if not row:
            raise StrategyError(f"strategy {version} not found")
        if row["status"] not in ("PRODUCTION", "APPROVED"):
            raise StrategyError(f"only APPROVED/PRODUCTION strategies can be activated (got {row['status']})")
        self.db.kv_set("active_strategy_version", version)

    def next_version(self, base: str) -> str:
        m = re.match(r"v(\d+)\.(\d+)", base)
        if m:
            return f"v{m.group(1)}.{int(m.group(2)) + 1}"
        return base + ".1"

    # ---- proposals ---------------------------------------------------
    def create_proposal(self, base_version: str, rules: list[dict], *, created_by: str, rationale: str = "",
                        name: str | None = None, version: str | None = None) -> dict:
        base = self.get(base_version)
        if not base:
            raise StrategyError(f"base strategy {base_version} not found")
        self.validate_rules(rules)
        proposed_version = version or self.next_version(base_version)
        bump = 1
        while self.get(proposed_version) and version is None:
            bump += 1
            proposed_version = self.next_version(f"v{proposed_version.lstrip('v')}")
            if bump > 50:
                raise StrategyError("cannot derive a free version number")
        self.db.execute(
            "INSERT INTO proposals (ts, created_by, base_version, proposed_version, rules_json, status, rationale) "
            "VALUES (?,?,?,?,?,?,?)",
            (iso_utc(), created_by, base_version, proposed_version, json.dumps(rules, ensure_ascii=False),
             "PROPOSAL", rationale),
        )
        return {
            "proposed_version": proposed_version,
            "base_version": base_version,
            "status": "PROPOSAL",
            "rules": rules,
            "rationale": rationale,
        }

    def list_proposals(self, status: str | None = None) -> list[dict]:
        if status:
            return self.db.query("SELECT * FROM proposals WHERE status=? ORDER BY id DESC", (status,))
        return self.db.query("SELECT * FROM proposals ORDER BY id DESC")

    def promote_proposal(self, proposed_version: str) -> dict:
        rows = self.db.query("SELECT * FROM proposals WHERE proposed_version=? ORDER BY id DESC", (proposed_version,))
        if not rows:
            raise StrategyError(f"proposal {proposed_version} not found")
        p = rows[0]
        if not self.get(proposed_version):
            self.create(proposed_version, name=f"proposal from {p['base_version']}",
                        rules=json.loads(p["rules_json"]), base_version=p["base_version"],
                        created_by=p["created_by"], status="PROPOSAL", origin="chat")
        return self.get(proposed_version) or {}

    def record_performance(self, version: str, pnl: float, won: bool) -> None:
        self.db.execute(
            "INSERT INTO strategy_performance (version, trades, wins, losses, pnl, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(version) DO UPDATE SET trades=trades+1, "
            "wins=wins+?, losses=losses+?, pnl=pnl+?, updated_at=?",
            (version, 1, 1 if won else 0, 0 if won else 1, pnl, iso_utc(),
             1 if won else 0, 0 if won else 1, pnl, iso_utc()),
        )
