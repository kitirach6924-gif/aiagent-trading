import { useEffect, useState } from "react";
import { api, type V1State } from "../lib/api";

const CURVE_DISPLAY: Record<string, { arrow: string; cls: string; label: string }> = {
  TURNING_UP: { arrow: "↗", cls: "green", label: "TURNING UP" },
  TURNING_DOWN: { arrow: "↘", cls: "red", label: "TURNING DOWN" },
  NO_TURN: { arrow: "→", cls: "amber", label: "NO TURN" },
  UNCLEAR: { arrow: "?", cls: "amber", label: "UNCLEAR" },
};

/** Strategy V1 panel: Stochastic 24,24,10 + curve + chart context + why-no-trade (spec §26–27) */
export default function StochasticPanel({ symbol }: { symbol: string }) {
  const [state, setState] = useState<V1State | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.v1State().then((s) => alive && setState(s)).catch(() => undefined);
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!state) return null;
  const st = state.stochastic ?? {};
  const curve = CURVE_DISPLAY[st.curve ?? "UNCLEAR"] ?? CURVE_DISPLAY.UNCLEAR;
  const chart = state.chart_context ?? {};
  const pa = chart.price_action ?? state.price_action_snapshot;
  const last = state.last_decision;

  return (
    <div className="card agent-card">
      <div className="panel-title">
        📈 Stochastic {state.timeframe} <span className={`badge ${curve.cls}`}>{curve.arrow} {curve.label}</span>
      </div>
      <div className="tiny agent-meta">
        <div>K: <b>{st.k ?? "—"}</b> · D: <b>{st.d ?? "—"}</b> <span className="muted">(prev {st.previous_k ?? "—"})</span></div>
        {st.reason && <div className="muted">{st.reason}</div>}
        {chart.decision && (
          <div>
            Chart: <span className={`badge ${chart.decision === "CONFIRM" ? "green" : chart.decision === "REJECT" ? "red" : "amber"}`}>{chart.decision}</span>{" "}
            {chart.trend} · {chart.structure}
            {typeof chart.signal_quality === "number" && <> · quality {chart.signal_quality.toFixed(2)}</>}
          </div>
        )}
        {chart.summary && <div className="muted">{chart.summary}</div>}
        {pa && (
          <div className="pa-block tiny">
            {"probe" in pa && Boolean((pa as { probe?: boolean }).probe) && <div className="muted">(price-action snapshot — รอสัญญาณ curve turn สำหรับการประเมินจริง)</div>}
            <div>Structure: <b>{pa.market_structure?.trend}</b> ({pa.market_structure?.state})
              {pa.market_structure?.sequence?.length ? <> · {pa.market_structure.sequence.join("+")}</> : null}</div>
            <div>Location: <b>{pa.price_location?.zone}</b> · Candle: <b>{pa.candlestick?.behavior}</b>
              {pa.candlestick?.pattern && pa.candlestick.pattern !== "NONE" && <> ({pa.candlestick.pattern})</>}</div>
            {pa.breakout && String(pa.breakout.state) !== "NONE" && <div>Breakout: <b>{String(pa.breakout.state)}</b>{pa.breakout?.confirmed ? " ✓" : " (unconfirmed)"}</div>}
            <div className="muted">
              support {pa.support_score?.toFixed(2)} · contra {pa.contradiction_score?.toFixed(2)} · uncertain {pa.uncertainty_score?.toFixed(2)}
            </div>
            {pa.reason_codes?.length > 0 && <div className="muted">{pa.reason_codes.slice(0, 6).join(" · ")}</div>}
          </div>
        )}
      </div>

      {/* §27: why didn't it trade */}
      {last && (
        <div className="why-block tiny">
          <div className="panel-title" style={{ fontSize: "0.8rem" }}>Why didn't it trade?</div>
          <div>
            Signal: <b>{st.curve ?? "—"}</b> → Agent: <b className={last.decision === "BUY" ? "green" : last.decision === "SELL" ? "red" : "amber"}>{last.decision}</b>
            {last.risk_result && <> → Risk Gate: <b className={last.risk_result === "PASS" ? "green" : "red"}>{last.risk_result}</b></>}
          </div>
          {last.reason && <div className="muted">{last.reason}</div>}
        </div>
      )}
      {state.error && <div className="muted tiny">state error: {state.error}</div>}
      <div className="muted tiny">strategy {state.strategy_version} · {state.autonomous ? "AUTONOMOUS" : "manual"} · {symbol}</div>
    </div>
  );
}
