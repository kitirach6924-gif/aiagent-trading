import { useCallback, useEffect, useState } from "react";
import { api, type AuditEvent } from "../lib/api";

export default function Audit() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [err, setErr] = useState("");

  const refresh = useCallback(() => {
    api.audit(200).then((r) => setEvents(r.events)).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <div className="card error">{err}</div>;

  return (
    <div className="card">
      <h3>Audit log (append-only, agent cannot delete)</h3>
      <div style={{ maxHeight: 520, overflowY: "auto" }}>
        <table>
          <thead><tr><th>Time</th><th>Action</th><th>Result</th><th>Agent</th><th>User</th><th>Strategy</th></tr></thead>
          <tbody>
            {events.map((e) => (
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
  );
}
