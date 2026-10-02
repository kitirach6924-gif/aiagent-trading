import { useCallback, useEffect, useState } from "react";
import { api, type LearningSummary } from "../lib/api";

export default function Learning() {
  const [data, setData] = useState<LearningSummary | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>("");

  const refresh = useCallback(() => {
    api.learningSummary().then(setData).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function runReview() {
    setBusy(true); setResult("");
    try {
      const r: any = await api.learningReview();
      setResult(JSON.stringify(r, null, 1).slice(0, 600));
      refresh();
    } catch (e) { setResult(String(e)); } finally { setBusy(false); }
  }

  if (err) return <div className="card error">{err}</div>;
  if (!data) return <div className="center">Loading…</div>;
  const s = data.summary;

  return (
    <div className="grid">
      <div className="card">
        <div className="panel-title">Learning engine — active: {data.active || "—"}</div>
        <div className="grid cols-3">
          <div>
            <div className="tiny muted box-title">Overall</div>
            <KV k="Trades" v={String(s.trades ?? 0)} />
            <KV k="Win rate" v={`${s.win_rate ?? "—"}%`} />
            <KV k="Total P/L" v={String(s.total_pnl ?? 0)} cls={(s.total_pnl ?? 0) >= 0 ? "green" : "red"} />
          </div>
          <div>
            <div className="tiny muted box-title">Per strategy</div>
            {data.per_strategy.length === 0 ? <p className="tiny muted">—</p> : data.per_strategy.map((p) => (
              <KV key={p.version} k={`${p.version} (${p.trades}t)`} v={String(p.pnl)} cls={p.pnl >= 0 ? "green" : "red"} />
            ))}
          </div>
          <div>
            <div className="tiny muted box-title">Recent losers</div>
            {data.recent_losers.length === 0 ? <p className="tiny muted">—</p> : data.recent_losers.slice(0, 5).map((t) => (
              <div key={t.id} className="tiny">{t.symbol} {t.side} <span className="red">{t.pnl}</span> ({t.exit_reason})</div>
            ))}
          </div>
        </div>
        <div className="controls">
          <button className="btn primary" onClick={runReview} disabled={busy}>
            {busy ? "reviewing…" : "Run learning review → propose improvements"}
          </button>
        </div>
        <p className="muted tiny">Learning Engine เสนอกฎใหม่เป็น PROPOSAL เท่านั้น — ไม่มีสิทธิ์ approve หรือแก้ production</p>
        {result && <pre className="tiny pre">{result}</pre>}
      </div>
    </div>
  );
}

function KV({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return <div className="kv small"><span>{k}</span><b className={cls}>{v}</b></div>;
}
