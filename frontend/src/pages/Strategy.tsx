import { useCallback, useEffect, useState } from "react";
import { api, type BacktestResult, type StrategiesResponse } from "../lib/api";

const STATUS_BADGE: Record<string, string> = {
  PRODUCTION: "green", APPROVED: "green", ACTIVE: "green",
  PROPOSAL: "amber", TESTING: "blue", VALIDATED: "blue", REJECTED: "red", ARCHIVED: "",
};

export default function Strategy() {
  const [data, setData] = useState<StrategiesResponse | null>(null);
  const [bt, setBt] = useState<BacktestResult | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.strategies().then(setData).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function runBacktest(version: string) {
    setBusy(version); setBt(null);
    try { setBt(await api.backtest(version)); } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  if (err) return <div className="card error">{err}</div>;
  if (!data) return <div className="center">Loading…</div>;

  return (
    <div className="grid">
      <div className="card">
        <div className="panel-title">Strategy versions — active: <span className="blue">{data.active || "none"}</span></div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>Version</th><th>Name</th><th>Status</th><th>Rules</th><th>Origin</th><th>Created</th><th>Actions</th></tr></thead>
            <tbody>
              {data.strategies.map((s) => {
                const rules = JSON.parse(s.rules_json) as Array<{ type: string; params?: any }>;
                return (
                  <tr key={s.version} className="clickable" onClick={() => setSelected(selected === s.version ? null : s.version)}>
                    <td><b>{s.version}</b>{s.version === data.active && <span className="badge green">ACTIVE</span>}</td>
                    <td>{s.name}</td>
                    <td><span className={`badge ${STATUS_BADGE[s.status] ?? "blue"}`}>{s.status}</span></td>
                    <td className="muted">{rules.map((r) => r.type).join(", ")}</td>
                    <td className="muted">{s.origin}</td>
                    <td className="muted">{s.created_at?.slice(0, 16)}</td>
                    <td>
                      <button className="btn small" disabled={busy === s.version}
                        onClick={(e) => { e.stopPropagation(); runBacktest(s.version); }}>
                        {busy === s.version ? "testing…" : "Backtest"}
                      </button>{" "}
                      <button className="btn small"
                        disabled={s.status !== "APPROVED" && s.status !== "PRODUCTION"}
                        onClick={(e) => { e.stopPropagation(); api.activateStrategy(s.version).then(refresh); }}>
                        Activate
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted tiny">Activation ได้เฉพาะ APPROVED/PRODUCTION — approval เป็นของมนุษย์เท่านั้น</p>
      </div>

      {selected && (() => {
        const s = data.strategies.find((x) => x.version === selected)!;
        const rules = JSON.parse(s.rules_json) as Array<{ type: string; params: any }>;
        const buy = rules.filter((r) => ["EMA_CROSS", "RANGE_BREAKOUT"].includes(r.type));
        const filters = rules.filter((r) => ["RSI_FILTER", "SPREAD_FILTER", "SESSION_FILTER"].includes(r.type));
        const stops = rules.filter((r) => ["ATR_STOP"].includes(r.type));
        return (
          <div className="card">
            <div className="panel-title">{s.version} — rule detail</div>
            <div className="grid cols-3">
              <RuleBox title="Entry rules (direction)" rules={buy} />
              <RuleBox title="No-trade filters" rules={filters} />
              <RuleBox title="Stop / risk" rules={stops} />
            </div>
          </div>
        );
      })()}

      <div className="card">
        <div className="panel-title">Proposals</div>
        {data.proposals.length === 0 ? <p className="muted">No pending proposals</p> : (
          <table className="data-table">
            <thead><tr><th>Proposed</th><th>Base</th><th>Status</th><th>Rationale</th><th>By</th></tr></thead>
            <tbody>
              {data.proposals.map((p) => (
                <tr key={p.id}>
                  <td>{p.proposed_version}</td><td>{p.base_version}</td>
                  <td><span className="badge amber">{p.status}</span></td>
                  <td className="muted">{p.rationale?.slice(0, 90)}</td>
                  <td className="muted">{p.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {bt && (
        <div className="card">
          <div className="panel-title">Backtest: {bt.strategy_version} vs baseline {bt.baseline_version}</div>
          <table className="data-table">
            <thead><tr><th>Metric</th><th>Baseline {bt.baseline_version}</th><th>Proposal {bt.strategy_version}</th></tr></thead>
            <tbody>
              <tr><td>Trades</td><td>{bt.baseline.metrics.trades}</td><td>{bt.proposal.metrics.trades}</td></tr>
              <tr><td>Win rate</td><td>{bt.baseline.metrics.win_rate}%</td><td>{bt.proposal.metrics.win_rate}%</td></tr>
              <tr><td>Total P/L</td><td>{bt.baseline.metrics.total_pnl}</td><td>{bt.proposal.metrics.total_pnl}</td></tr>
              <tr><td>Max drawdown</td><td>{bt.baseline.metrics.max_drawdown}</td><td>{bt.proposal.metrics.max_drawdown}</td></tr>
              <tr><td>Final equity</td><td>{bt.baseline.metrics.final_equity}</td><td>{bt.proposal.metrics.final_equity}</td></tr>
            </tbody>
          </table>
          <p className="muted tiny">Factual metrics only — ระบบไม่เลือกผู้ชนะให้ การตัดสินใจเป็นของคุณ</p>
        </div>
      )}
    </div>
  );
}

function RuleBox({ title, rules }: { title: string; rules: Array<{ type: string; params: any }> }) {
  return (
    <div>
      <div className="tiny muted box-title">{title}</div>
      {rules.length === 0 ? <p className="tiny muted">—</p> : rules.map((r, i) => (
        <div key={i} className="kv small">
          <span>{r.type}</span><code className="tiny">{JSON.stringify(r.params ?? {})}</code>
        </div>
      ))}
    </div>
  );
}
