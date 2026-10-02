import { useEffect, useState } from "react";
import { api } from "../lib/api";

export interface ProviderInfo {
  id: string;
  name: string;
  symbol_map: Record<string, string>;
  notes: string;
  broker_server_hint: string;
  login_url: string;
  builtin: boolean;
}

export interface ProvidersResponse {
  active: string;
  providers: ProviderInfo[];
  mt5_mode: string;
}

/** Broker provider menu (spec: เมนูเลือก provider ของ MT5) — mapping เท่านั้น,
 *  การ login account ยังทำใน MT5 terminal; สลับแล้ว verify สถานะจริงทันที */
export default function ProviderMenu({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<ProvidersResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => api.providers().then(setData).catch(() => undefined);
  useEffect(() => { load(); }, []);

  async function select(pid: string) {
    setBusy(true); setMsg("");
    try {
      const r = await api.selectProvider(pid);
      setMsg(`✓ ใช้ ${r.provider.name} — MT5 ${r.mt5.connected ? "เชื่อมต่ออยู่" : "ยังไม่ตอบ (เช็ค terminal)"}${r.mt5.terminal?.name ? ` · ${r.mt5.terminal.name}` : ""}`);
      load();
      onChanged?.();
    } catch (e) {
      setMsg(`✗ ${String(e).slice(0, 140)}`);
    } finally { setBusy(false); }
  }

  async function reconnect() {
    setBusy(true); setMsg("");
    try {
      const r = await api.mt5Reconnect();
      setMsg(r.ok ? "✓ bridge ใหม่พร้อม — terminal ตอบสนอง"
                  : `✗ reconnect ล้มเหลว: ${r.error ?? ""}`.slice(0, 140));
      onChanged?.();
    } catch (e) {
      setMsg(`✗ ${String(e).slice(0, 140)}`);
    } finally { setBusy(false); }
  }

  const active = data?.providers.find((p) => p.id === data?.active);

  return (
    <div className="card agent-card">
      <div className="panel-title">🔌 MT5 Provider</div>
      <div className="controls compact" style={{ flexWrap: "wrap" }}>
        {(data?.providers ?? []).map((p) => (
          <button key={p.id}
            className={`btn small ${p.id === data?.active ? "primary" : ""}`}
            disabled={busy || p.id === data?.active}
            title={`${p.notes}\nserver: ${p.broker_server_hint || "—"}`}
            onClick={() => select(p.id)}>
            {p.name}{p.id === data?.active ? " ✓" : ""}
          </button>
        ))}
        <button className="btn small" disabled={busy} onClick={reconnect}>🔄 Reconnect</button>
      </div>
      {active && (
        <div className="tiny agent-meta muted">
          <div>Gold symbol: <b>{active.symbol_map?.XAUUSD ?? "XAUUSD"}</b> · server: {active.broker_server_hint || "—"}</div>
          {active.notes && <div>{active.notes}</div>}
        </div>
      )}
      {msg && <div className="tiny" style={{ marginTop: 6 }}>{msg}</div>}
      <div className="muted tiny">การ login account ยังอยู่ใน MT5 terminal — เมนูนี้เลือกโบรกเกอร์/map symbol ให้ระบบใช้ถูกตัว</div>
    </div>
  );
}
