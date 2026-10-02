"""Chat Agent: classifies intent deterministically first, then answers.

Cost control: READ/CONTROL intents are handled with deterministic code + no LLM
(or a cheap chat model only for phrasing). Analysis intents may use the router.
"""
from __future__ import annotations

import json
import re

from app.ai.model_router import router
from app.config import settings
from app.core.agents import MarketAgent
from app.core.db import Database
from app.core.events import Event, bus, iso_utc
from app.core.journal import StatisticsEngine, TradeJournal
from app.core.strategies import StrategyRegistry, StrategyError
from app.integrations.telegram import TelegramNotifier

INTENTS = ["READ", "ANALYSIS", "STRATEGY", "CONTROL", "CHAT"]


class ChatError(Exception):
    pass


class ChatAgent:
    def __init__(self, db: Database, connector, registry: StrategyRegistry,
                 journal: TradeJournal, stats: StatisticsEngine, notifier: TelegramNotifier,
                 loop_ref=None, backtester_ref=None) -> None:
        self.db = db
        self.connector = connector
        self.registry = registry
        self.journal = journal
        self.stats = stats
        self.notifier = notifier
        self._loop_ref = loop_ref
        self._backtester_ref = backtester_ref
        from app.core.strategy_engine import StrategyEngine
        self._engine = StrategyEngine(registry)

    # ---------- persistence ----------
    def log_message(self, role: str, text: str, user: str | None = None, intent: str | None = None,
                    meta: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO chat_messages (ts, role, user, text, intent, meta_json) VALUES (?,?,?,?,?,?)",
            (iso_utc(), role, user, text, intent, json.dumps(meta or {}, ensure_ascii=False)))

    def history(self, limit: int = 100) -> list[dict]:
        return self.db.query(
            "SELECT ts, role, user, text, intent FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- classification (deterministic first) ----------
    @staticmethod
    def classify(text: str) -> tuple[str, str]:
        t = text.lower()
        if re.search(r"emergency|ฉุกเฉิน", t):
            return "CONTROL", "control"
        strategy_patterns = [
            r"(เพิ่มกฎ|add (a )?rule|เปลี่ยนกฎ|change (the )?rule|แก้กฎ)",
            r"(สร้าง|create|make).*(strategy|กลยุทธ์|v\d+\.\d+)",
            r"(backtest|เทส|ทดสอบ).*(v\d+\.\d+|strategy)?",
            r"(เอา|deploy|promote).*(v\d+\.\d+).*(demo|production|โปรดักชัน)?",
            r"(อนุมัติ|approve|reject|ปฏิเสธ)",
        ]
        for pat in strategy_patterns:
            if re.search(pat, t):
                return "STRATEGY", "strategy"
        control_patterns = [
            r"(หยุด|stop|pause).*(เทรด|trading|ออเดอร์|order)",
            r"^(resume|หยุดพัก|กลับมาเทรด|continue)",
            r"resume trading",
        ]
        for pat in control_patterns:
            if re.search(pat, t):
                return "CONTROL", "control"
        analysis_patterns = [
            r"(วิเคราะห์|analyze|analysis|ทำไม|why|เพราะอะไร)",
            r"(แพ้|lose|losing|กำไร|profit|สถิติ|statistic|performance)",
        ]
        for pat in analysis_patterns:
            if re.search(pat, t):
                return "ANALYSIS", "analysis"
        read_patterns = [
            r"(สถานะ|status|ตลาด|market|ราคา|price)",
            r"(เทรดไปกี่|เทรดกี่|how many|กี่ครั้ง|กี่ trade)",
            r"(position|positional|ออเดอร์|order)",
            r"(account|บัญชี)",
            r"^(hi|hello|สวัสดี|hey)",
        ]
        for pat in read_patterns:
            if re.search(pat, t):
                return "READ", "read"
        return "CHAT", "chat"

    # ---------- main entry ----------
    async def handle(self, text: str, user: str = "user") -> dict:
        self.log_message("user", text, user=user)
        await bus.publish(Event(action="AI_MESSAGE_RECEIVED", result=text[:120], user=user, agent="chat"))
        intent, _kind = self.classify(text)
        try:
            # Every reply opens with the live context block so the human and the agent
            # are always reasoning about the same numbers (balance, price, day
            # direction, indicators, signals, open positions, stats).
            ctx = await self.live_context()
            if intent == "READ":
                reply = await self._handle_read(text)
            elif intent == "ANALYSIS":
                reply = await self._handle_analysis(text, ctx)
            elif intent == "STRATEGY":
                reply = await self._handle_strategy(text, user)
            elif intent == "CONTROL":
                reply = await self._handle_control(text, user)
            else:
                reply = await self._handle_chat(text, ctx)
            if ctx:
                reply = f"{ctx}\n\n{reply}"
        except ChatError as e:
            reply = f"❌ {e}"
        except Exception as e:  # noqa: BLE001
            reply = f"❌ เกิดข้อผิดพลาด: {e}"
        self.log_message("agent", reply, user="agent", intent=intent)
        return {"reply": reply, "intent": intent}

    async def live_context(self) -> str:
        """Compact live state header. Read-only, never trades, never throws."""
        try:
            from app.core.monitor import build_monitor_snapshot
            snap = await build_monitor_snapshot(self.connector, source=self._source(),
                                                loop_status=self._loop_status(), db=self.db)
        except Exception:  # noqa: BLE001
            return ""
        acc = snap.get("account") or {}
        px = snap.get("price") or {}
        day = snap.get("day") or {}
        sig = snap.get("signals") or {}
        st = (snap.get("indicators") or {}).get("stochastic") or {}
        curve = (snap.get("indicators") or {}).get("curve") or {}
        pos = snap.get("positions") or []
        stats = (snap.get("stats") or {}).get("today") or {}

        arrow = {"UP": "▲", "DOWN": "▼", "FLAT": "▬"}.get(day.get("direction") or "", "•")
        day_txt = (f"{arrow} {day.get('direction') or '—'} "
                   f"{day.get('change_percent') if day.get('change_percent') is not None else '—'}%") \
            if day.get("direction") else "day —"
        lines = [
            f"🧭 บริบทปัจจุบัน · {snap.get('symbol')} · {snap.get('timeframe')} · "
            f"{snap.get('data_source')}",
            f"💰 {acc.get('mode','?')} balance {acc.get('balance')} equity {acc.get('equity')} "
            f"margin {acc.get('margin_used')} free {acc.get('free_margin')}",
            f"📈 ราคา bid {px.get('bid')} / ask {px.get('ask')} · วันนี้ {day_txt}",
            f"🎯 Indicator: %K {sig.get('k')} / %D {sig.get('d')} ({sig.get('k_zone')}) · "
            f"curve {sig.get('curve')} ({sig.get('curve_reason')})",
            f"🧠 สัญญาณ: เข้า={sig.get('entry')} · ออก={sig.get('exit_side') or '—'} · "
            f"เหตุผล: {'; '.join((sig.get('entry_reasons') or ['—'])[:2])}",
            f"📋 positions {len(pos)}"
            + (f" · SL ขาด: {sig.get('positions_without_stop')}" if sig.get("positions_without_stop") else "")
            + f" · วันนี้ {stats.get('trades',0)} trades PnL {stats.get('pnl',0)}",
        ]
        if pos:
            lines.append("   " + "; ".join(
                f"{p['side']} {p['lot']}@{p['entry_price']} pnl {p['pnl']}"
                f"{' (ไม่มี SL)' if not p['has_stop'] else ''}" for p in pos[:4]))
        return "\n".join(lines)

    def _source(self) -> str:
        try:
            from app.main import connector_source  # type: ignore
            return connector_source
        except Exception:  # noqa: BLE001
            return "UNKNOWN"

    def _loop_status(self) -> dict:
        ref = self._loop_ref() if self._loop_ref else None
        return getattr(ref, "status", {}) or {}

    # ---------- READ ----------
    @staticmethod
    def _symbol_hint(t: str) -> str:
        """Pick the symbol the user mentioned, else the first configured one.
        Handles broker symbol aliases (XAUUSD ↔ GOLD)."""
        configured = settings.symbols or ["GOLD"]
        if "xauusd" in t or "gold" in t or "ทอง" in t:
            for s in configured:
                if "GOLD" in s.upper() or "XAU" in s.upper():
                    return s
            return "GOLD"
        return configured[0]

    async def _affordability_note(self, symbol: str) -> str:
        """Can the balance open one minimum lot right now? Read-only."""
        try:
            info = await self.connector.get_symbol_info(symbol)
            acc = await self.connector.get_account_status()
            px = await self.connector.get_price(symbol)
            contract = info.contract_size or (
                100.0 if symbol.upper().find("XAU") >= 0 or symbol.upper().find("GOLD") >= 0
                else 100_000.0)
            min_lot = info.min_lot or info.lot_step or 0.01
            need = min_lot * (px.ask or 0) * contract
            if acc.equity >= need:
                return (f"✅ พอเปิด — ขั้นต่ำ {min_lot} lot ต้องใช้ ~{need:,.0f} "
                        f"มี equity {acc.equity:,.2f}")
            short = need - acc.equity
            return (f"❌ ไม่พอ — ขั้นต่ำ {min_lot} lot ของ {symbol} ต้องใช้ ~{need:,.0f} "
                    f"แต่มี equity {acc.equity:,.2f} (ขาด ~{short:,.0f})\n"
                    f"   ต้องเติมเงิน หรือเทรดสัญลักษณ์อื่นที่ขั้นต่ำถูกกว่า")
        except Exception as e:  # noqa: BLE001
            return f"ตรวจไม่สำเร็จ: {type(e).__name__}"

    async def _handle_read(self, text: str) -> str:
        t = text.lower()
        parts: list[str] = []
        if re.search(r"(ตลาด|market|ราคา|price|xauusd|gold|สถานะ)", t):
            snap = await MarketAgent(self.connector).snapshot(self._symbol_hint(t))
            ind = snap["indicators"]
            p = snap["price"]
            parts.append(
                f"📊 {snap['symbol']}: bid {p['bid']} / ask {p['ask']} (spread {p['spread_points']})\n"
                f"RSI14 {ind['rsi14'] and round(ind['rsi14'], 1)} | EMA9 {ind['ema9'] and round(ind['ema9'], 5)} | "
                f"EMA21 {ind['ema21'] and round(ind['ema21'], 5)} | ATR14 {ind['atr14'] and round(ind['atr14'], 5)}")
        if re.search(r"(เทรดไปกี่|เทรดกี่|how many|กี่ครั้ง|กี่ trade|วันนี้|today)", t):
            today = self.stats.today()
            parts.append(f"📈 วันนี้: {today['trades']} trades, wins {today['wins']}, PnL {today['pnl']}")
        if re.search(r"(position|ออเดอร์|order)", t):
            positions = await self.connector.get_positions()
            if positions:
                lines = [f"• {p.symbol} {p.side} {p.lot} lots @ {p.entry_price} pnl {round(p.pnl, 2)}" for p in positions]
                parts.append("Open positions:\n" + "\n".join(lines))
            else:
                parts.append("ไม่มี position ที่เปิดอยู่")
        if re.search(r"(พอ|พอไหม|พอเปิด|ยอด|เงิน|มาร์จิ้น|margin|ขั้นต่ำ|min lot|afford)", t):
            acc = await self.connector.get_account_status()
            aff = await self._affordability_note(self._symbol_hint(t))
            parts.append(f"💰 {acc.mode} account: balance {acc.balance:.2f}, equity {acc.equity:.2f}"
                         + (f"\n{aff}" if aff else ""))
        if re.search(r"(account|บัญชี)", t) and not parts:
            acc = await self.connector.get_account_status()
            parts.append(f"💰 {acc.mode} account {acc.login}: balance {acc.balance:.2f}, equity {acc.equity:.2f}")
        if re.search(r"(strategy|กลยุทธ์)", t):
            parts.append(f"🧠 Active strategy: {self.registry.active_version() or '(none set)'}")
        if not parts:
            status = await self.connector.get_status()
            parts.append(
                f"🤖 Agent online | MT5 mode {settings.mt5_mode} ({'connected' if status.get('connected') else 'offline'})\n"
                f"Strategy: {self.registry.active_version() or '(none)'} | Mode: {settings.trading_mode}\n"
                "ถามได้ เช่น 'สถานะตลาดตอนนี้เป็นอย่างไร', 'วันนี้เทรดไปกี่ครั้ง', 'เปิด position ไหม'")
        return "\n\n".join(parts)

    # ---------- ANALYSIS ----------
    async def _handle_analysis(self, text: str, ctx: str = "") -> str:
        t = text.lower()
        # "ทำไมยังไม่ BUY/SELL" → explain the live decision path deterministically.
        # The side word is optional: "ทำไมวันนี้ถึงไม่เข้าเทรด" asks the same question
        # without naming a side, and used to fall through to the ask-back string.
        m = re.search(r"ทำไม.*(?:ไม่|ยังไม่)\s*(buy|sell|ขาย|ซื้อ|เข้า|เทรด|trade|order|ออเดอร์)?", t)
        if m:
            # No side named (e.g. "ทำไมวันนี้ถึงไม่เข้าเทรด") means "why is it not
            # trading", not "why not SELL" — report whatever the live signal is.
            named = m.group(1)
            want = ("BUY" if re.search(r"buy|ซื้อ", named or "", re.I)
                    else "SELL" if re.search(r"sell|ขาย", named or "", re.I)
                    else None)
            from app.core.agents import MarketAgent, StrategyAgent
            symbol = self._symbol_hint(t)
            snap = await MarketAgent(self.connector).snapshot(symbol)
            sig = await StrategyAgent(self._engine, self.registry).evaluate(
                self.registry.active_version() or settings.default_strategy_id, snap)
            last = self.db.query_one(
                "SELECT reason, decision FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,))
            if want:
                lines = [f"🔎 เหตุผลล่าสุดที่ระบบยังไม่ {want} ({symbol}):"]
            else:
                lines = [f"🔎 เหตุผลที่ระบบยังไม่เข้าเทรด ({symbol}):"]
            if last:
                lines.append(f"• การตัดสินใจล่าสุด: {last['decision']} — {last['reason']}")
            lines.append(f"• สัญญาณ strategy ตอนนี้: {sig.signal} ({sig.reason})")
            if sig.conditions_failed:
                lines.append("• เงื่อนไขที่ยังไม่ผ่าน: " + ", ".join(sig.conditions_failed))
            positions = await self.connector.get_positions()
            if positions:
                lines.append(f"• มี position เปิดอยู่ {len(positions)} รายการ — ระบบไม่เข้าซ้ำทิศทางเดียวกัน")
            else:
                lines.append("• ยังไม่มี position เปิดอยู่")
            lines.append("• ทุก order ต้องผ่าน Risk Gate; หากถูกบล็อกจะมีเหตุผลบันทึกใน audit log")
            return "\n".join(lines)
        if re.search(r"(แพ้|lose|losing)", t):
            trades = self.journal.losing_trades(10)
            if not trades:
                return "ยังไม่มี trade ที่แพ้ใน journal"
            lines = [f"• {tr['symbol']} {tr['side']} {tr['lot']} pnl {tr['pnl']} ({tr['exit_reason']}) "
                     f"v{tr['strategy_version']}" for tr in trades]
            base = "📉 Trade ที่แพ้ล่าสุด:\n" + "\n".join(lines)
            ai = await router.complete("trade_analysis",
                                       system="You are a trading performance analyst. Be concise, factual, no hype.",
                                       user=f"Analyze these losing trades for patterns:\n{lines}")
            if ai:
                return base + "\n\n🔍 AI analysis:\n" + ai["content"]
            return base + "\n\n(ตั้งค่า LLM_API_KEY เพื่อให้ AI วิเคราะห์เชิงลึกเพิ่ม)"
        if re.search(r"(สถิติ|statistic|performance|กำไร)", t):
            s = self.stats.summary()
            return "📊 Statistics:\n" + json.dumps(s, ensure_ascii=False, indent=1)
        if re.search(r"(ย้อนหลัง|back|history|xauusd)", t):
            snap = await MarketAgent(self.connector).snapshot(settings.symbols[0], count=120)
            candles = snap["candles"][-20:]
            closes = [round(c["close"], 5) for c in candles]
            ai = await router.complete("research",
                                       system="You are a market analyst. Summarize recent price action factually in <=120 words.",
                                       user=f"Recent closes for {snap['symbol']}: {closes}")
            trend = "up" if closes[-1] > closes[0] else "down"
            out = f"📈 {snap['symbol']} ย้อนหลัง 20 แท่งล่าสุด: trend {trend} ({closes[0]} → {closes[-1]})"
            if ai:
                out += "\n\n🔍 AI: " + ai["content"]
            return out
        # Unknown analysis question. Do not hand back an ask-back menu: the live
        # context is real state the user can act on, so show it and name what
        # this handler can actually answer.
        if ctx:
            return (f"ผมยังไม่ตอบเจาะจงในหัวข้อนี้ได้ครับ แต่นี่คือสถานะล่าสุดที่ผมอ่านได้:\n\n{ctx}\n\n"
                    f"หัวข้อที่ตอบได้ตรง ๆ: trade ที่แพ้, สถิติ/กำไร, กราฟย้อนหลัง, "
                    f"ทำไมยังไม่เข้า BUY/SELL")
        return ("ผมยังไม่ตอบเจาะจงในหัวข้อนี้ได้ครับ และยังไม่มีสถานะให้อ้างอิง — "
                "ตั้ง LLM_API_KEY ใน .env แล้ว restart container เพื่อให้ตอบได้ทุกคำถาม")

    # ---------- STRATEGY (chat-first management) ----------
    async def _handle_strategy(self, text: str, user: str) -> str:
        t = text.lower()
        # "เพิ่มกฎว่า ..." → structured proposal
        if re.search(r"(เพิ่มกฎ|add (a )?rule)", t):
            rules: list[dict] = []
            if re.search(r"spread", t):
                rules.append({"type": "SPREAD_FILTER", "params": {"max_points": settings.max_spread_points}})
            if re.search(r"(rsi)", t):
                rules.append({"type": "RSI_FILTER", "params": {"period": 14, "oversold": 35, "overbought": 65}})
            if re.search(r"(session|ช่วงเวลา)", t):
                rules.append({"type": "SESSION_FILTER", "params": {"allowed": settings.allowed_sessions}})
            if not rules:
                raise ChatError("ยังแปลงกฎนี้เป็น structured rule ไม่ได้ (รองรับ: spread / RSI / session ตอนนี้)")
            base = self.registry.active_version() or "v1.0"
            try:
                prop = self.registry.create_proposal(base, rules, created_by=user,
                                                     rationale=f"from chat: {text[:200]}")
            except StrategyError as e:
                raise ChatError(str(e))
            await bus.publish(Event(action="STRATEGY_PROPOSAL_CREATED", result=prop["proposed_version"],
                                    user=user, agent="chat", strategy_version=prop["proposed_version"],
                                    payload=prop))
            r = rules[0]
            rule_name = {"SPREAD_FILTER": "SPREAD_FILTER", "RSI_FILTER": "RSI_FILTER", "SESSION_FILTER": "SESSION_FILTER"}[r["type"]]
            return (
                "เข้าใจแล้ว ✅\n\n"
                f"Proposed Rule: {rule_name}\n"
                f"Condition: {json.dumps(r['params'], ensure_ascii=False)}\n"
                "Action: NO_TRADE when violated\n"
                f"Target: Strategy {prop['proposed_version']} (base {base})\n"
                "Status: PROPOSAL — ยังไม่มีผลกับการเทรดจนกว่าจะ approve\n"
                "พิมพ์ 'ทดสอบ v" + prop["proposed_version"].lstrip("v") + "' เพื่อ backtest")

        # "ทดสอบ vX.Y" / "backtest vX.Y" → run backtest
        m = re.search(r"(?:ทดสอบ|backtest|เทส)\s*(v?\d+\.\d+)?", t)
        if m and re.search(r"(ทดสอบ|backtest|เทส)", t):
            ver = m.group(1)
            ver = ("v" + ver.replace("v", "")) if ver else None
            if not ver:
                return "ระบุ version ที่จะทดสอบ เช่น 'ทดสอบ v1.1'"
            bt = self._backtester_ref() if self._backtester_ref else None
            if bt is None:
                raise ChatError("backtester ยังไม่พร้อม")
            await bus.publish(Event(action="BACKTEST_STARTED", result=ver, user=user, agent="chat", strategy_version=ver))
            result = await bt.run(ver)
            await bus.publish(Event(action="BACKTEST_COMPLETED", result=ver, agent="backtest",
                                    strategy_version=ver, payload={"metrics": result.get("proposal", {}).get("metrics")}))
            base = result.get("baseline", {})
            prop = result.get("proposal", {})
            bm, pm = base.get("metrics", {}), prop.get("metrics", {})
            return (
                f"🧪 BACKTEST_COMPLETED {ver}\n\n"
                f"Baseline {result.get('baseline_version')}: trades {bm.get('trades')}, win% {bm.get('win_rate')}, "
                f"PnL {bm.get('total_pnl')}, maxDD {bm.get('max_drawdown')}\n"
                f"Proposal {ver}: trades {pm.get('trades')}, win% {pm.get('win_rate')}, "
                f"PnL {pm.get('total_pnl')}, maxDD {pm.get('max_drawdown')}\n\n"
                "ข้อมูลเป็นเพียง factual metrics — การเลือกใช้เป็นดุลยพินิจของคุณ "
                "พิมพ์ 'อนุมัติ " + ver + "' เพื่อส่งต่อ approval")

        # "อนุมัติ vX.Y" → promote to registry (still needs human approve → APPROVED)
        m = re.search(r"(อนุมัติ|approve)\s*(v?\d+\.\d+)", t)
        if m:
            ver = "v" + m.group(2).replace("v", "")
            self.registry.promote_proposal(ver)
            self.registry.set_status(ver, "APPROVED", approved_by=user)
            await bus.publish(Event(action="STRATEGY_APPROVED", result=ver, user=user, agent="chat", strategy_version=ver))
            return f"✅ {ver} ถูกอนุมัติโดย {user} แล้ว พิมพ์ 'ใช้ {ver}' เพื่อเป็น active strategy"

        # "ปฏิเสธ vX.Y"
        m = re.search(r"(ปฏิเสธ|reject)\s*(v?\d+\.\d+)", t)
        if m:
            ver = "v" + m.group(2).replace("v", "")
            self.registry.set_status(ver, "REJECTED")
            await bus.publish(Event(action="STRATEGY_REJECTED", result=ver, user=user, agent="chat", strategy_version=ver))
            return f"❌ {ver} ถูกปฏิเสธ"

        # "ใช้ vX.Y" → activate
        m = re.search(r"(ใช้|use|activate)\s*(v?\d+\.\d+)", t)
        if m:
            ver = "v" + m.group(2).replace("v", "")
            try:
                self.registry.promote_proposal(ver)
                self.registry.set_active_version(ver)
            except StrategyError as e:
                raise ChatError(str(e))
            await bus.publish(Event(action="STRATEGY_ACTIVATED", result=ver, user=user, agent="chat", strategy_version=ver))
            return f"🎯 Active strategy ตอนนี้คือ {ver}"

        # "สร้าง strategy v1.1" → clone active with a tweak (proposal only)
        m = re.search(r"สร้าง\s*(?:strategy\s*)?(v?\d+\.\d+)", t)
        if m:
            target = "v" + m.group(1).replace("v", "")
            base = self.registry.active_version() or "v1.0"
            try:
                rules = self.registry.rules(base)
                self.registry.create_proposal(base, rules, created_by=user, version=target,
                                              rationale="cloned from chat")
            except StrategyError as e:
                raise ChatError(str(e))
            return f"สร้าง proposal {target} จาก {base} แล้ว (status PROPOSAL) — บอกกฎที่จะเพิ่ม เช่น 'เพิ่มกฎว่า spread สูงให้หยุดเทรด'"

        return ("ผมช่วยเรื่อง strategy ได้ เช่น:\n"
                "• 'เพิ่มกฎว่า spread สูงให้หยุดเทรด' → สร้าง proposal\n"
                "• 'ทดสอบ v1.1' → backtest baseline vs proposal\n"
                "• 'อนุมัติ v1.1' / 'ใช้ v1.1' / 'ปฏิเสธ v1.1'")

    # ---------- CONTROL ----------
    async def _handle_control(self, text: str, user: str) -> str:
        t = text.lower()
        if re.search(r"emergency|ฉุกเฉิน", t):
            self.db.kv_set("emergency_stop", True)
            await bus.publish(Event(action="EMERGENCY_STOP", result="on", user=user, agent="chat"))
            return "🚨 EMERGENCY STOP activated — ห้ามเปิดออเดอร์ใหม่ทั้งหมด (Risk Gate บล็อกทุก order)"
        if re.search(r"(หยุด|stop|pause)", t) and not re.search(r"(resume|กลับมา|แค่)", t):
            self.db.kv_set("paused", True)
            await bus.publish(Event(action="TRADING_PAUSED", result="on", user=user, agent="chat"))
            return "⏸️ Trading paused — ไม่เปิดออเดอร์ใหม่ (position เดิมยังถูกจัดการตาม SL/TP)"
        if re.search(r"(resume|กลับมา|continue|หยุดพัก)", t):
            self.db.kv_set("paused", False)
            await bus.publish(Event(action="TRADING_RESUMED", result="off", user=user, agent="chat"))
            return "▶️ Trading resumed"
        return "คำสั่งควบคุม: 'หยุดเปิดออเดอร์ใหม่' / 'Resume Trading' / 'Emergency stop'"

    # ---------- CHAT fallback (cheap model, short) ----------
    async def _handle_chat(self, text: str, ctx: str = "") -> str:
        """Free-form chat. Falls back to a real answer from live state.

        Previously the only fallback was a menu string, so with no LLM key every
        question came back as "ถามเรื่อง market/positions/statistics ได้ครับ" —
        the agent already held the live context but discarded it. The fallback
        now answers from the state it actually has, and only says it cannot help
        when there is genuinely no state to answer from.
        """
        ai = await router.complete(
            "chat",
            system=("You are the chat interface of an autonomous MT5 DEMO trading agent. "
                    "A LIVE CONTEXT block precedes the user's message — always ground your answer in it "
                    "and never invent numbers that are not in it. The context shows: balance/equity, "
                    "current price and today's direction, the %K/%D stochastic values, the curve state, "
                    "the current entry/exit signal with reasons, open positions, and today's stats.\n"
                    "Explain what the agent is waiting for and what would trigger entry or exit. "
                    "Be concise and answer in the user's language (Thai if they write Thai). "
                    "You can READ state and PROPOSE — you cannot execute trades or change risk settings."),
            user=(f"[LIVE CONTEXT]\n{ctx}\n\n[USER] {text}" if ctx else text), max_tokens=600)
        if ai and ai.get("content", "").strip():
            return ai["content"]
        # LLM unavailable. Answer from live state rather than asking the user
        # to rephrase — an answer grounded in real numbers beats a menu.
        if ctx:
            return (f"ผมตอบจากสถานะปัจจุบันแบบตรงไปตรงมา (ยังไม่ได้ต่อ LLM — "
                    f"ตั้ง LLM_API_KEY เพื่อให้ตอบเชิงวิเคราะห์ได้ละเอียดขึ้น)\n\n{ctx}\n\n"
                    f"คำถามของคุณคือ “{text.strip()}” — "
                    f"ถ้าต้องการให้เจาะจงเรื่องใด บอกได้เลยครับ เช่น “ทำไมยังไม่เข้า BUY” "
                    f"หรือ “วันนี้ PnL เป็นอย่างไร”")
        return ("ยังตอบคำถามอิสระไม่ได้ครับ เพราะยังไม่ได้ต่อ LLM และยังไม่มีสถานะเรื่อง "
                "ให้อ่าน — ระบบจะตอบได้ทันทีที่ต่อ LLM หรือมีข้อมูลบัญชีพร้อม "
                "(ตั้ง LLM_API_KEY ใน .env แล้ว restart container)")
