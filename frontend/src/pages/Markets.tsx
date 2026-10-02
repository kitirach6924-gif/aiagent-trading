import { api, type CandlesResponse } from "../lib/api";
import { useTicks } from "../lib/live";
import { useEffect, useState } from "react";

export default function Markets() {
  const ticks = useTicks();
  const [symbols, setSymbols] = useState<string[]>([]);
  const [snaps, setSnaps] = useState<Record<string, CandlesResponse | null>>({});

  useEffect(() => {
    api.systemStatus().then((s) => setSymbols(s.symbols?.length ? s.symbols : ["GOLD"])).catch(() => undefined);
  }, []);

  useEffect(() => {
    symbols.forEach((s) => {
      api.candles(s, "M15", 60).then((snap) => setSnaps((p) => ({ ...p, [s]: snap }))).catch(() => setSnaps((p) => ({ ...p, [s]: null })));
    });
  }, [symbols]);

  return (
    <div className="card">
      <div className="panel-title">Markets</div>
      <table className="data-table">
        <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Spread</th><th>RSI14</th><th>EMA9</th><th>EMA21</th><th>ATR14</th></tr></thead>
        <tbody>
          {symbols.map((s) => {
            const t = ticks[s];
            const n = snaps[s]?.indicators_now;
            return (
              <tr key={s}>
                <td><b>{s}</b> {t?.stale && <span className="badge amber">stale</span>}</td>
                <td className="red">{t ? t.bid.toFixed(2) : "—"}</td>
                <td className="green">{t ? t.ask.toFixed(2) : "—"}</td>
                <td>{t?.spread ?? "—"}</td>
                <td>{n?.rsi14 != null ? n.rsi14.toFixed(1) : "—"}</td>
                <td>{n?.ema9 != null ? n.ema9.toFixed(2) : "—"}</td>
                <td>{n?.ema21 != null ? n.ema21.toFixed(2) : "—"}</td>
                <td>{n?.atr14 != null ? n.atr14.toFixed(2) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
