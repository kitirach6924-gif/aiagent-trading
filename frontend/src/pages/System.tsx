import { useCallback, useEffect, useState } from "react";
import { api, type AuditEvent, type SystemStatus } from "../lib/api";
import { live, useLive, type Heartbeat } from "../lib/live";
import { StatusDot } from "../components/StatusDot";

export default function System() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [lastError, setLastError] = useState("");
  const [tickAge, setTickAge] = useState<number | null>(null);
  const [connectedSince, setConnectedSince] = useState<number>(Date.now());
  const hb = useLive<Heartbeat>("HEARTBEAT");
  const [tg, setTg] = useState<{ configured: boolean; token_set: boolean; chat_id_set: boolean } | null>(null);
  const [tgToken, setTgToken] = useState("");
  const [tgChat, setTgChat] = useState("");
  const [tgMsg, setTgMsg] = useState("");
  const [tgBusy, setTgBusy] = useState(false);

  const refresh = useCallback(() => {
    api.systemStatus().then(setStatus).catch((e) => setLastError(String(e)));
    api.audit(60).then((r) => setAudit(r.events)).catch(() => undefined);
    api.telegramStatus().then(setTg).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    const live0 = live.subscribe((m) => {
      if (m.type === "MARKET_TICK") setTickAge(0);
      if (m.type === "HEARTBEAT") setLastError("");
    });
    return () => { clearInterval(t); live0(); };
  }, [refresh]);

  useEffect(() => {
    const t = setInterval(() => setTickAge((a) => (a == null ? a : a + 1)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => setConnectedSince(Date.now()), []);

  const uptime = Math.round((Date.now() - connectedSince) / 1000);
  const agentRunning = hb?.agent.running ?? status?.agent.running;
  const mt5 = hb?.mt5 ?? status?.mt5;
  const abnormal = !agentRunning || !(mt5?.connected);

  return (
    <div className="grid">
      {abnormal && <div className="banner error">⚠ System abnormal — agent {agentRunning ? "running" : "STOPPED"}, MT5 {mt5?.connected ? "connected" : "DISCONNECTED"}</div>}
      <div className="grid cols-2">
        <div className="card">
          <div className="panel-title">Services</div>
          <StatusDot on={!!agentRunning} label="TRADING AGENT" warn="AGENT OFFLINE" />
          <StatusDot on={!!mt5?.connected} label="MT5" warn="MT5 DISCONNECTED" />
          <StatusDot on={hb?.mcp.connected ?? status?.mcp.mode === "MCP"} label="MCP" />
          <StatusDot on={live.connected} label="WEBSOCKET" warn="WS DOWN (reconnecting)" />
          <StatusDot on={lastError === ""} label="API" warn="API ERROR" />
          <div className="tiny muted services-extra">
            <div>FIREBASE: {status ? <span className="green">CONNECTED (mirror live)</span> : <span className="amber">CHECKING…</span>}</div>
            <div>TELEGRAM: {tg?.configured ? <span className="green">CONNECTED</span> : <span className="amber">NOT CONFIGURED — setup below</span>}</div>
            <div>DATABASE: <span className="green">SQLite (local WAL)</span></div>
            <div>AI PROVIDER: <span className="amber">deterministic mode (no key)</span></div>
          </div>
        </div>
        <div className="card">
          <div className="panel-title">Telegram Setup</div>
          <div className="tiny muted" style={{ marginBottom: 8 }}>
            1) คุยกับ @BotFather → /newbot → คัดลอก token · 2) กด Start กับบอท · 3) รัน <code>python infra/telegram/get_chat_id.py &lt;token&gt;</code> · 4) ทดสอบส่งด้านล่าง
          </div>
          <div className="controls compact" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <input placeholder="bot token (123456:AAH...) — ไม่บันทึก ใช้ทดสอบครั้งเดียว"
                   value={tgToken} onChange={(e) => setTgToken(e.target.value)} type="password" />
            <input placeholder="chat_id (ดูจาก get_chat_id.py)"
                   value={tgChat} onChange={(e) => setTgChat(e.target.value)} />
            <button className="btn small primary" disabled={tgBusy || (!tgToken && !tg?.configured) || !tgChat}
                    onClick={async () => {
                      setTgBusy(true); setTgMsg("");
                      try {
                        await api.telegramTest(tgToken || undefined, tgChat || undefined);
                        setTgMsg("✓ ส่งสำเร็จ — เช็คแชท Telegram ของคุณ");
                        setTgToken("");
                        api.telegramStatus().then(setTg).catch(() => undefined);
                      } catch (e) { setTgMsg(`✗ ${String(e).slice(0, 160)}`); }
                      finally { setTgBusy(false); }
                    }}>
              ส่งข้อความทดสอบ
            </button>
            {tgMsg && <div className="tiny">{tgMsg}</div>}
            <div className="tiny muted">เมื่อทดสอบผ่าน: ใส่ค่าเดิมลง backend/.env เป็น TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID เพื่อใช้ถาวร</div>
          </div>
          <div className="panel-title">Runtime</div>
          <div className="kv small"><span>Trading mode</span><span className={`badge ${(hb?.trading_mode ?? status?.trading_mode) === "DEMO" ? "blue" : "red"}`}>{hb?.trading_mode ?? status?.trading_mode ?? "—"}</span></div>
          <div className="kv small"><span>Data source</span><span>{hb?.data_source ?? "—"}</span></div>
          <div className="kv small"><span>Cycles</span><span>{hb?.agent.cycles ?? status?.agent.cycles ?? "—"}</span></div>
          <div className="kv small"><span>Last heartbeat</span><span>{hb?.ts?.slice(11, 19) ?? status?.agent.last_beat?.slice(11, 19) ?? "—"}</span></div>
          <div className="kv small"><span>WS session uptime</span><span>{uptime >= 60 ? `${Math.floor(uptime / 60)}m ${uptime % 60}s` : `${uptime}s`}</span></div>
          <div className="kv small"><span>Last market update</span><span>{tickAge == null ? "—" : `${tickAge}s ago`}</span></div>
          <div className="kv small"><span>Last error</span><span className={lastError ? "red" : "muted"}>{lastError || "none"}</span></div>
        </div>
      </div>

      <div className="card">
        <div className="panel-title">Audit log (append-only)</div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>Time</th><th>Action</th><th>Result</th><th>Agent</th><th>User</th><th>Strategy</th></tr></thead>
            <tbody>
              {audit.map((e) => (
                <tr key={e.id}>
                  <td className="muted">{e.ts.slice(5, 19).replace("T", " ")}</td>
                  <td><span className="badge blue">{e.action}</span></td>
                  <td>{e.result?.slice(0, 60)}</td>
                  <td className="muted">{e.agent}</td>
                  <td className="muted">{e.user}</td>
                  <td className="muted">{e.strategy_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
