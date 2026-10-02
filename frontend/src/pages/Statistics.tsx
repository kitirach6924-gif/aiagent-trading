import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi, type ISeriesApi, type LineData, Time } from "lightweight-charts";
import { api, type EquityCurve, type StatisticsResponse } from "../lib/api";

export default function Statistics() {
  const [data, setData] = useState<StatisticsResponse | null>(null);
  const [curve, setCurve] = useState<EquityCurve | null>(null);
  const [err, setErr] = useState("");

  const refresh = useCallback(() => {
    api.statistics().then(setData).catch((e) => setErr(String(e)));
    api.equityCurve().then(setCurve).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <div className="card error">{err}</div>;
  if (!data) return <div className="center">Loading…</div>;
  const s = data.summary;
  const byStrategy = Object.entries(s.by_strategy ?? {});

  return (
    <div className="grid">
      <div className="grid cols-3">
        <Card title="Today">
          <KV k="Date" v={data.today.date} />
          <KV k="Trades" v={String(data.today.trades)} />
          <KV k="Wins" v={String(data.today.wins)} />
          <KV k="P/L" v={String(data.today.pnl)} cls={data.today.pnl >= 0 ? "green" : "red"} />
        </Card>
        <Card title="Overall">
          <KV k="Trades" v={String(s.trades ?? 0)} />
          <KV k="Win rate" v={`${s.win_rate ?? "—"}%`} />
          <KV k="Total P/L" v={String(s.total_pnl ?? 0)} cls={(s.total_pnl ?? 0) >= 0 ? "green" : "red"} />
          <KV k="Expectancy" v={String(s.expectancy ?? "—")} />
        </Card>
        <Card title="Risk quality">
          <KV k="Profit factor" v={String(s.profit_factor ?? "—")} />
          <KV k="Max drawdown" v={String(curve?.max_drawdown ?? s.max_drawdown ?? "—")} cls="red" />
          <KV k="Avg win" v={String(s.avg_win ?? "—")} cls="green" />
          <KV k="Avg loss" v={String(s.avg_loss ?? "—")} cls="red" />
        </Card>
      </div>

      <EquityChart curve={curve} />

      <div className="card">
        <div className="panel-title">Per strategy version</div>
        {byStrategy.length === 0 ? <p className="muted">No closed trades yet</p> : (
          <table className="data-table">
            <thead><tr><th>Version</th><th>Trades</th><th>Wins</th><th>P/L</th></tr></thead>
            <tbody>
              {byStrategy.map(([v, st]) => (
                <tr key={v}><td>{v}</td><td>{st.trades}</td><td>{st.wins}</td>
                  <td className={st.pnl >= 0 ? "green" : "red"}>{st.pnl}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <div className="panel-title">{title}</div>
      {children}
    </div>
  );
}

function KV({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return <div className="kv small"><span>{k}</span><b className={cls}>{v}</b></div>;
}

function EquityChart({ curve }: { curve: EquityCurve | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const eqRef = useRef<ISeriesApi<"Area"> | null>(null);
  const ddRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#857b9e", fontSize: 11 },
      grid: { vertLines: { color: "rgba(124,92,214,0.08)" }, horzLines: { color: "rgba(124,92,214,0.08)" } },
      rightPriceScale: { borderColor: "rgba(124,92,214,0.14)" },
      timeScale: { borderColor: "rgba(124,92,214,0.14)", timeVisible: true },
      autoSize: true,
    });
    chartRef.current = chart;
    eqRef.current = chart.addAreaSeries({ lineColor: "#17a673", topColor: "rgba(23,166,115,0.18)", lineWidth: 2, title: "Equity" });
    ddRef.current = chart.addAreaSeries({ lineColor: "#e05561", topColor: "rgba(224,85,97,0.14)", lineWidth: 1, title: "Drawdown" });
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!curve?.points?.length) return;
    const eq: LineData[] = [];
    const dd: LineData[] = [];
    curve.points.forEach((p, i) => {
      const t = (new Date(p.ts).getTime() / 1000 || i) as unknown as Time;
      eq.push({ time: t, value: p.equity });
      dd.push({ time: t, value: curve.current_equity + p.drawdown });
    });
    eqRef.current?.setData(eq);
    ddRef.current?.setData(dd);
    chartRef.current?.timeScale().fitContent();
  }, [curve]);

  return (
    <div className="card">
      <div className="panel-title">Equity curve & drawdown</div>
      {!curve?.points?.length ? <p className="muted">No closed trades yet — curve will appear after the first system trade closes.</p> : null}
      <div ref={ref} style={{ height: 260 }} />
    </div>
  );
}
