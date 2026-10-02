import { useEffect, useMemo, useState } from "react";
import type { SeriesMarker, Time } from "lightweight-charts";
import { api, type TradeDetail, type SystemStatus } from "../lib/api";
import { live, useEvents, useLive, useTicks, type AccountSnapshot, type Heartbeat } from "../lib/live";
import { watchCloudStatus, type CloudStatus } from "../lib/cloud";
import LiveChart from "../components/LiveChart";
import StochasticPanel from "../components/StochasticPanel";
import ProviderMenu from "../components/ProviderMenu";
import { Banner, StatusDot } from "../components/StatusDot";

export default function Dashboard() {
  const ticks = useTicks();
  const heartbeat = useLive<Heartbeat>("HEARTBEAT");
  const snapshot = useLive<AccountSnapshot>("ACCOUNT_SNAPSHOT");
  const events = useEvents(30);
  const [symbol, setSymbol] = useState("GOLD");
  const [symbols, setSymbols] = useState<string[]>(["GOLD"]);
  const [decision, setDecision] = useState<{ decision: string; reason: string; strategy_version: string; risk_result: string | null; ts: string } | null>(null);
  const [markerDetail, setMarkerDetail] = useState<TradeDetail | null>(null);
  const [restStatus, setRestStatus] = useState<SystemStatus | null>(null);
  const [apiDead, setApiDead] = useState(false);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);

  // Cloud View fallback: ถ้า API ไม่ตอบ (backend local/cloudrun เข้าไม่ถึง) อ่าน Firestore ตรง
  useEffect(() => {
    let alive = true;
    api.systemStatus().then(() => alive && setApiDead(false)).catch(() => alive && setApiDead(true));
    const t = setInterval(() => {
      api.systemStatus().then(() => alive && setApiDead(false)).catch(() => alive && setApiDead(true));
    }, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  useEffect(() => watchCloudStatus((s) => setCloud(s)), []);
  const [confirmCloseAll, setConfirmCloseAll] = useState(false);

  useEffect(() => {
    api.systemStatus().then((s) => { setRestStatus(s); setSymbols(s.symbols?.length ? s.symbols : ["GOLD"]); }).catch(() => undefined);
    if (cloud?.symbols?.length && apiDead) setSymbols(cloud.symbols);
    api.decisions(1).then((d) => {
      if (d.decisions[0]) setDecision(d.decisions[0]);
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    return live.subscribe((msg) => {
      if (msg.type === "EVENT" && (msg.action === "TRADE_SIGNAL" || msg.action === "RISK_BLOCK")) {
        api.decisions(1).then((d) => d.decisions[0] && setDecision(d.decisions[0])).catch(() => undefined);
      }
    });
  }, []);

  const tick = ticks[symbol] ?? null;
  const marketStale = !tick || tick.stale;
  const hb = heartbeat ?? (restStatus ? {
    agent: restStatus.agent, mt5: restStatus.mt5, mcp: restStatus.mcp,
    trading_mode: restStatus.trading_mode, live_trading: restStatus.live_trading,
    symbols: restStatus.symbols, ts: restStatus.agent.last_beat,
  } as Heartbeat : null);

  const positions: Array<{ ticket: string; symbol: string; side: string; lot: number; entry_price: number; current_price?: number; pnl: number; sl?: number; tp?: number }> =
    snapshot?.positions ?? (apiDead && cloud?.positions ? cloud.positions : []);
  const account = snapshot?.account ?? (apiDead && cloud?.account ? {
    login: "cloud", name: "", currency: cloud.account.currency ?? "USD",
    balance: cloud.account.balance ?? 0, equity: cloud.account.equity ?? 0,
    margin_used: 0, leverage: 0, server: cloud.account.server ?? "", mode: cloud.account.mode ?? "DEMO",
  } : null);
  const floating = positions.reduce((s, p) => s + p.pnl, 0);
  const exposure = positions.reduce((s, p) => s + p.lot, 0);

  // trade markers from journal
  const markers = useMemo(async () => {
    const r = await api.trades(50);
    return r.trades
      .filter((t) => t.symbol === symbol && t.entry_price)
      .slice(0, 40)
      .map((t) => ({
        time: (new Date(t.ts_open).getTime() / 1000) as unknown as Time,
        position: t.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
        color: t.side === "BUY" ? "#17a673" : "#e05561",
        shape: t.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
        text: `${t.side} ${t.lot}`,
        tradeId: t.id,
      })) as SeriesMarker<Time>[];
  }, [symbol, events.length]);

  const [markerList, setMarkerList] = useState<SeriesMarker<Time>[]>([]);
  useEffect(() => { markers.then(setMarkerList); }, [markers]);

  const signal = decision?.decision ?? "WAIT";
  const signalClass = signal === "BUY" ? "green" : signal === "SELL" ? "red" : "amber";

  function onMarkerClick(d: TradeDetail) {
    setMarkerDetail(d);
  }

  return (
    <div className="workspace">
      {apiDead && (
        <Banner kind="warn">
          ☁ CLOUD VIEW — เชื่อม API ตรงไม่ได้ แสดงข้อมูลจาก Firestore (อัปเดต ~30s): agent {cloud?.agent?.running ? "RUNNING" : "—"}{cloud?.agent?.cycles != null ? ` · cycles ${cloud.agent.cycles}` : ""}{cloud?.runner ? ` · runner: ${cloud.runner}` : ""}
        </Banner>
      )}
      {hb && !hb.mt5.connected && (
        <Banner kind="error">⚠ MT5 CONNECTION LOST — Trading paused by safety gate. Waiting for reconnection…</Banner>
      )}
      {marketStale && (
        <Banner kind="warn">MARKET DATA STALE — last update: {tick ? new Date(tick.ts).toLocaleTimeString() : "—"}</Banner>
      )}
      {hb?.agent.emergency_stop && <Banner kind="error">🚨 EMERGENCY STOP ACTIVE — ทุก order ถูกบล็อก</Banner>}
      {hb?.agent.paused && !hb?.agent.emergency_stop && <Banner kind="warn">⏸ Trading paused</Banner>}

      <div className="ws-main">
        <section className="ws-chart card">
          <div className="ws-chart-head">
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className="sym-select">
              {(symbols.length ? symbols : ["GOLD"]).map((s) => <option key={s}>{s}</option>)}
            </select>
            {hb && <span className={`badge ${hb.data_source === "LIVE" ? "red" : hb.data_source === "DEMO" ? "blue" : "amber"}`}>{hb.data_source ?? "DEMO"}</span>}
          </div>
          <LiveChart symbol={symbol} tick={tick} markers={markerList} onMarkerClick={onMarkerClick}
            sltp={positions.find((p) => p.symbol === symbol) ? { sl: positions.find((p) => p.symbol === symbol)!.sl, tp: positions.find((p) => p.symbol === symbol)!.tp, entry: positions.find((p) => p.symbol === symbol)!.entry_price } : null} />
          {markerDetail && (
            <div className="marker-detail card">
              <div className="marker-detail-head">
                <b>Trade #{markerDetail.trade.id}</b>
                <button className="btn small" onClick={() => setMarkerDetail(null)}>✕</button>
              </div>
              <div className="grid cols-2 tiny">
                <div>Symbol: {markerDetail.trade.symbol} {markerDetail.trade.side} {markerDetail.trade.lot}</div>
                <div>Strategy: {markerDetail.trade.strategy_version}</div>
                <div>Entry: {markerDetail.trade.entry_price} → Exit: {markerDetail.trade.exit_price ?? "—"}</div>
                <div>SL: {markerDetail.trade.sl} / TP: {markerDetail.trade.tp}</div>
                <div>P/L: <span className={(markerDetail.trade.pnl ?? 0) >= 0 ? "green" : "red"}>{markerDetail.trade.pnl ?? "—"}</span></div>
                <div>Reason: {markerDetail.trade.exit_reason ?? "open"}</div>
                <div>Decision: {markerDetail.decision?.decision} (risk: {markerDetail.decision?.risk_result ?? "—"})</div>
                <div>Opened: {markerDetail.trade.ts_open?.slice(0, 19)}</div>
              </div>
            </div>
          )}
        </section>

        <aside className="ws-side">
          <div className="card market-panel">
            <div className="panel-title">{symbol} <span className={`badge ${signalClass}`}>{signal}</span></div>
            {tick && !tick.stale ? (
              <div className="price-grid">
                <div><label>Bid</label><b className="red">{tick.bid.toFixed(2)}</b></div>
                <div><label>Ask</label><b className="green">{tick.ask.toFixed(2)}</b></div>
                <div><label>Spread</label><span>{tick.spread}</span></div>
              </div>
            ) : <p className="muted small">price {marketStale ? "stale…" : "…"}</p>}
            {decision && (
              <div className="kv small"><span>Strategy</span><span>{decision.strategy_version}</span></div>
            )}
            {decision && (
              <div className="kv small"><span>Last decision</span><span className={`badge ${decision.risk_result === "PASS" ? "green" : decision.risk_result === "BLOCK" ? "red" : "amber"}`}>{decision.decision} · {decision.risk_result ?? "—"}</span></div>
            )}
            {decision?.reason && <p className="muted tiny reason">{decision.reason}</p>}
          </div>

          <div className="card">
            <div className="panel-title">Account</div>
            {account ? (
              <>
                <div className="kv small"><span>Balance</span><b>{account.balance.toFixed(2)} {account.currency}</b></div>
                <div className="kv small"><span>Equity</span><b className={account.equity >= account.balance ? "green" : "red"}>{account.equity.toFixed(2)}</b></div>
                <div className="kv small"><span>Floating P/L</span><b className={floating >= 0 ? "green" : "red"}>{floating >= 0 ? "+" : ""}{floating.toFixed(2)}</b></div>
                <div className="kv small"><span>Exposure</span><span>{exposure.toFixed(2)} lots</span></div>
              </>
            ) : <p className="muted small">waiting snapshot…</p>}
          </div>

          <StochasticPanel symbol={symbol} />

          <ProviderMenu onChanged={() => { api.systemStatus().then(setRestStatus).catch(() => undefined); }} />

          <div className="card agent-card">
            <div className="panel-title">AI Agent <StatusDot on={!!hb?.agent.running || !!restStatus?.agent.running} label="ONLINE" /></div>
            {hb && (
              <div className="tiny agent-meta">
                <div>Strategy: <b>{hb.agent.active_strategy ?? "—"}</b></div>
                <div>Decision: <b className={signalClass}>{signal}</b></div>
                <div>Cycles: {hb.agent.cycles} · Beat: {hb.agent.last_beat?.slice(11, 19)}</div>
              </div>
            )}
            <div className="controls compact">
              <button className="btn small amber-btn" onClick={() => api.pause(true)}>🟡 Pause</button>
              <button className="btn small" onClick={() => api.pause(false)}>▶ Resume</button>
              <button className="btn small" onClick={() => setConfirmCloseAll(true)}>🧹 Close all</button>
              <button className="btn small danger" onClick={() => setConfirmStop(true)}>🔴 EMERGENCY STOP</button>
            </div>
          </div>
        </aside>
      </div>

      <section className="ws-bottom">
        <div className="card">
          <div className="panel-title">Open positions ({positions.length})</div>
          {positions.length === 0 ? <p className="muted small">No open positions</p> : (
            <table className="data-table">
              <thead><tr><th>Symbol</th><th>Side</th><th>Lot</th><th>Entry</th><th>SL</th><th>TP</th><th>P/L</th></tr></thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.ticket}>
                    <td>{p.symbol}</td>
                    <td className={p.side === "BUY" ? "green" : "red"}>{p.side}</td>
                    <td>{p.lot}</td>
                    <td>{p.entry_price}</td>
                    <td>{p.sl || "—"}</td>
                    <td>{p.tp || "—"}</td>
                    <td className={p.pnl >= 0 ? "green" : "red"}>{p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <div className="panel-title">Live events</div>
          <div className="event-feed">
            {events.length === 0 && <p className="muted small">Waiting for events…</p>}
            {events.slice(0, 12).map((e) => (
              <div key={e.seq} className="tiny">
                <span className="muted">{e.ts.slice(11, 19)}</span> <span className={`badge ${e.action.includes("RISK") || e.action.includes("EMERGENCY") ? "red" : e.action.includes("TRADE") ? "green" : "blue"}`}>{e.action}</span> {e.result?.slice(0, 50)}
              </div>
            ))}
          </div>
        </div>
      </section>

      {confirmCloseAll && (
        <div className="modal-backdrop" onClick={() => setConfirmCloseAll(false)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3>🧹 Confirm CLOSE ALL</h3>
            <p>ปิด position ที่ระบบกำลังดูแลทั้งหมดทันที ({positions.length} ตำแหน่ง)</p>
            <div className="controls">
              <button className="btn danger" onClick={() => { api.closeAll(true); setConfirmCloseAll(false); }}>ยืนยันปิดทั้งหมด</button>
              <button className="btn" onClick={() => setConfirmCloseAll(false)}>ยกเลิก</button>
            </div>
          </div>
        </div>
      )}

      {confirmStop && (
        <div className="modal-backdrop" onClick={() => setConfirmStop(false)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3>🚨 Confirm EMERGENCY STOP</h3>
            <p>ระบบจะบล็อกการเปิดออเดอร์ใหม่ทั้งหมดทันที (position เดิมยังอยู่)</p>
            <div className="controls">
              <button className="btn danger" onClick={() => { api.emergencyStop(true); setConfirmStop(false); }}>ยืนยัน EMERGENCY STOP</button>
              <button className="btn" onClick={() => setConfirmStop(false)}>ยกเลิก</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
