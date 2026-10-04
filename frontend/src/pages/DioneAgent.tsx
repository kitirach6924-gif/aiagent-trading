/**
 * DIONE Agent page — embeds the static DIONE Agent Panel served from /dione.html
 * (frontend/public/dione.html). Read-only display: DEMO / READ-ONLY / EXECUTION BLOCKED.
 * No execution, no credentials, no fabricated live data — the panel labels every
 * data source with its real provenance (VERIFIED / PARTIAL / UNVERIFIED / BLOCKED).
 */
export default function DioneAgent() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8 }}>
      <div className="card" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700 }}>🧠 DIONE Agent Panel</span>
        <span className="badge blue">DEMO</span>
        <span className="badge amber">READ-ONLY</span>
        <span className="badge red">EXECUTION BLOCKED</span>
        <a href="/dione.html" target="_blank" rel="noreferrer" className="btn small" style={{ marginLeft: "auto" }}>
          Open full view ↗
        </a>
      </div>
      <iframe
        src="/dione.html"
        title="DIONE Agent Panel"
        style={{
          flex: 1,
          width: "100%",
          minHeight: "70vh",
          border: "1px solid var(--border, #ddd)",
          borderRadius: 12,
          background: "#fff",
        }}
      />
    </div>
  );
}
