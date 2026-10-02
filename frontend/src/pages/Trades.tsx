import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TradeDetail, type TradesResponse } from "../lib/api";

export default function Trades() {
  const [data, setData] = useState<TradesResponse | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [side, setSide] = useState("");
  const [result, setResult] = useState("");
  const [detail, setDetail] = useState<TradeDetail | null>(null);

  const refresh = useCallback(() => {
    api.trades(300).then(setData).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.trades.filter((t) => {
      if (q && !`${t.symbol} ${t.strategy_version} ${t.ticket}`.toLowerCase().includes(q.toLowerCase())) return false;
      if (side && t.side !== side) return false;
      if (result === "win" && (t.pnl ?? 0) <= 0) return false;
      if (result === "loss" && (t.pnl ?? 0) > 0) return false;
      return true;
    });
  }, [data, q, side, result]);

  function duration(t: any): string {
    if (!t.ts_close) return "open";
    const ms = new Date(t.ts_close).getTime() - new Date(t.ts_open).getTime();
    const m = Math.round(ms / 60000);
    return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
  }

  if (err) return <div className="card error">{err}</div>;
  if (!data) return <div className="center">Loading…</div>;

  return (
    <div className="grid">
      <div className="card">
        <div className="filters">
          <input placeholder="Search symbol / ticket / strategy…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={side} onChange={(e) => setSide(e.target.value)}>
            <option value="">Side: all</option><option>BUY</option><option>SELL</option>
          </select>
          <select value={result} onChange={(e) => setResult(e.target.value)}>
            <option value="">Result: all</option><option value="win">Win</option><option value="loss">Loss</option>
          </select>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Lot</th><th>Entry</th><th>Exit</th><th>SL</th><th>TP</th><th>Strategy</th><th>Reason</th><th>Duration</th><th>P/L</th></tr></thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => api.tradeDetail(t.id).then(setDetail).catch(() => undefined)}>
                  <td className="muted">{t.ts_close?.slice(5, 16).replace("T", " ")}</td>
                  <td>{t.symbol}</td>
                  <td className={t.side === "BUY" ? "green" : "red"}>{t.side}</td>
                  <td>{t.lot}</td>
                  <td>{t.entry_price}</td>
                  <td>{t.exit_price ?? "—"}</td>
                  <td>{t.sl ?? "—"}</td>
                  <td>{t.tp ?? "—"}</td>
                  <td className="muted">{t.strategy_version}</td>
                  <td>{t.exit_reason ?? "—"}</td>
                  <td className="muted">{duration(t)}</td>
                  <td className={(t.pnl ?? 0) >= 0 ? "green" : "red"}>{t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}` : "—"}</td>
                </tr>
              ))}
              {filtered.length === 0 && <tr><td colSpan={12} className="muted">No trades match</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {detail && (
        <div className="card">
          <div className="marker-detail-head">
            <b>Trade #{detail.trade.id} — full trace</b>
            <button className="btn small" onClick={() => setDetail(null)}>✕</button>
          </div>
          <div className="grid cols-2 tiny">
            <div>Symbol: {detail.trade.symbol} {detail.trade.side} {detail.trade.lot}</div>
            <div>Strategy: {detail.trade.strategy_version} ({detail.trade.mode})</div>
            <div>Ticket: {detail.trade.ticket}</div>
            <div>Entry {detail.trade.entry_price} @ {detail.trade.ts_open?.slice(0, 19)}</div>
            <div>SL {detail.trade.sl ?? "—"} / TP {detail.trade.tp ?? "—"}</div>
            <div>Exit {detail.trade.exit_price ?? "—"} ({detail.trade.exit_reason ?? "open"})</div>
            <div>Decision: {detail.decision?.decision ?? "—"} · Risk {detail.decision?.risk_result ?? "—"}</div>
            <div>P/L: <b className={(detail.trade.pnl ?? 0) >= 0 ? "green" : "red"}>{detail.trade.pnl ?? "—"}</b></div>
          </div>
          {detail.decision?.reason && <p className="tiny muted">Decision reason: {detail.decision.reason}</p>}
          {detail.open_event && <p className="tiny muted">Audit: TRADE_OPEN {detail.open_event.ts.slice(0, 19)} by {detail.open_event.agent}</p>}
          {detail.close_event && <p className="tiny muted">Audit: TRADE_CLOSE {detail.close_event.ts.slice(0, 19)} — {detail.close_event.result}</p>}
        </div>
      )}
    </div>
  );
}
