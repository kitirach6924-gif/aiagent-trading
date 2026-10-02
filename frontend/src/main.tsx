import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { loginGoogle, loginEmail, logout, watchAuth, firebaseConfigured } from "./lib/firebase";
import { live, useConnection, useEvents, useLive, type Heartbeat } from "./lib/live";
import { StatusDot } from "./components/StatusDot";
import Dashboard from "./pages/Dashboard";
import Markets from "./pages/Markets";
import Trades from "./pages/Trades";
import Statistics from "./pages/Statistics";
import Strategy from "./pages/Strategy";
import Learning from "./pages/Learning";
import System from "./pages/System";
import AgentChatPanel from "./components/AgentChatPanel";
import "./styles.css";

type Tab = "dashboard" | "markets" | "trading" | "strategies" | "trades" | "analytics" | "learning" | "system";

const NAV: Array<{ id: Tab; label: string; icon: string; mobile?: boolean }> = [
  { id: "dashboard", label: "Dashboard", icon: "📊", mobile: true },
  { id: "markets", label: "Markets", icon: "🌍" },
  { id: "trading", label: "Trading", icon: "⚡", mobile: true },
  { id: "strategies", label: "Strategies", icon: "🧠", mobile: true },
  { id: "trades", label: "Trades", icon: "📈", mobile: true },
  { id: "analytics", label: "Analytics", icon: "🧮" },
  { id: "learning", label: "Learning", icon: "🎓" },
  { id: "system", label: "System", icon: "🛡️" },
];

function App() {
  const [user, setUser] = useState<null | { email: string | null }>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [mobileChat, setMobileChat] = useState(false);
  const conn = useConnection();
  const hb = useLive<Heartbeat>("HEARTBEAT");
  const events = useEvents(40);
  const unseenCritical = events.filter((e) =>
    ["EMERGENCY_STOP", "RISK_BLOCK", "LOOP_ERROR", "TRADE_OPEN"].includes(e.action)).length;

  useEffect(() => watchAuth((u) => { setUser(u ? { email: u.email } : null); setChecking(false); }), []);
  useEffect(() => { if (!firebaseConfigured) setChecking(false); }, []);
  useEffect(() => { live.connect(); }, []);

  if (checking) return <div className="center">Loading…</div>;

  if (!user) {
    return (
      <div className="login-wrap">
        <div className="card login-card">
          <h1>🤖 AI Trading Agent</h1>
          <p className="muted">Demo trading only · fail-closed · human approval required for production</p>
          <button className="btn primary" disabled={!firebaseConfigured} onClick={() => loginGoogle().catch((e) => setErr(String(e)))}>
            Sign in with Google
          </button>
          <div className="divider">or email</div>
          <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn" disabled={!firebaseConfigured} onClick={() => loginEmail(email, password).catch((e) => setErr(String(e)))}>
            Sign in
          </button>
          <div className="divider">local development</div>
          <button className="btn small" onClick={() => setUser({ email: "local@dev" })}>
            Enter Local mode (no Firebase)
          </button>
          {err && <p className="error">{err}</p>}
        </div>
      </div>
    );
  }

  const tradingMode = hb?.trading_mode ?? "DEMO";

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="hamburger" onClick={() => setSidebarOpen((o) => !o)}>☰</button>
        <span className="brand">🤖 AI TRADING <span className={`badge ${tradingMode === "LIVE" ? "red" : "blue"}`}>{tradingMode} {hb?.live_trading ? "⚠" : "●"}</span></span>
        <div className="top-status">
          <StatusDot on={!!hb?.agent.running} label="AGENT" warn="AGENT OFFLINE" />
          <StatusDot on={!!hb?.mt5.connected} label="MT5" warn="MT5 DOWN" />
          <StatusDot on={!!hb?.mcp.connected} label="MCP" />
          <StatusDot on={conn} label="LIVE" warn="WS RECONNECTING" />
        </div>
        <div className="top-actions">
          <button className="icon-btn" onClick={() => setNotifOpen((o) => !o)} title="Notifications">
            🔔{unseenCritical > 0 && <span className="notif-dot">{unseenCritical > 9 ? "9+" : unseenCritical}</span>}
          </button>
          <span className="muted user-email">{user.email}</span>
          <button className="btn small" onClick={() => { logout(); setUser(null); }}>Sign out</button>
        </div>
      </header>

      {notifOpen && (
        <div className="notif-center card">
          <div className="panel-title">Notifications</div>
          {events.length === 0 && <p className="muted small">No events yet</p>}
          {events.slice(0, 15).map((e) => {
            const level = ["EMERGENCY_STOP", "LOOP_ERROR"].includes(e.action) ? "CRITICAL"
              : e.action.includes("RISK") ? "WARNING" : e.action.includes("TRADE") ? "TRADE" : "INFO";
            return (
              <div key={e.seq} className={`notif-row level-${level.toLowerCase()}`}>
                <span className="tiny muted">{e.ts.slice(11, 19)}</span>
                <span className={`badge ${level === "CRITICAL" ? "red" : level === "WARNING" ? "amber" : "blue"}`}>{level}</span>
                <span className="tiny">{e.action}</span>
                <span className="tiny muted ellipsis">{e.result?.slice(0, 40)}</span>
              </div>
            );
          })}
        </div>
      )}

      <div className="shell-body">
        <nav className={`sidebar ${sidebarOpen ? "open" : ""}`}>
          {NAV.map((n) => (
            <button key={n.id} className={`nav-item ${tab === n.id ? "active" : ""}`}
              onClick={() => { setTab(n.id); setSidebarOpen(false); }}>
              <span className="nav-icon">{n.icon}</span> {n.label}
            </button>
          ))}
        </nav>

        <main className={`content ${tab === "dashboard" ? "content-dash" : ""}`}>
          {tab === "dashboard" && <Dashboard />}
          {tab === "markets" && <Markets />}
          {tab === "trading" && <TradingTab />}
          {tab === "strategies" && <Strategy />}
          {tab === "trades" && <Trades />}
          {tab === "analytics" && <Statistics />}
          {tab === "learning" && <Learning />}
          {tab === "system" && <System />}
        </main>

        <aside className={`agent-rail ${mobileChat ? "mobile-open" : ""}`}>
          <div className="card agent-rail-card">
            <div className="panel-title">💬 AI Agent {hb?.agent.running && <span className="badge green">● ONLINE</span>}</div>
            {hb && (
              <div className="tiny agent-meta">
                <div>Strategy: <b>{hb.agent.active_strategy ?? "—"}</b></div>
                <div>Market: {hb.symbols?.join(", ")}</div>
              </div>
            )}
            <AgentChatPanel compact />
          </div>
        </aside>
      </div>

      <nav className="bottom-nav">
        {NAV.filter((n) => n.mobile).map((n) => (
          <button key={n.id} className={`bn-item ${tab === n.id ? "active" : ""}`} onClick={() => setTab(n.id)}>
            <span className="bn-icon">{n.icon}</span>
            <span className="bn-label">{n.label}</span>
          </button>
        ))}
        <button className={`bn-item ${mobileChat ? "active" : ""}`} onClick={() => setMobileChat((o) => !o)}>
          <span className="bn-icon">💬</span>
          <span className="bn-label">Chat</span>
        </button>
      </nav>
    </div>
  );
}

function TradingTab() {
  return (
    <div className="grid">
      <div className="card"><p>Order execution is handled by the autonomous agent only. Manual trading is intentionally not exposed — safety first.</p></div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
