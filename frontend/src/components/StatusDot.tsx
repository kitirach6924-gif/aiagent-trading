export function StatusDot({ on, label, warn }: { on: boolean | undefined; label: string; warn?: string }) {
  return (
    <span className="status-pill" title={on ? label : warn ?? label}>
      <span className={`dot ${on ? "on" : "off"}`} />
      <span className="dot-label">{label}</span>
    </span>
  );
}

export function Banner({ kind, children }: { kind: "warn" | "error" | "info"; children: React.ReactNode }) {
  return <div className={`banner ${kind}`}>{children}</div>;
}
