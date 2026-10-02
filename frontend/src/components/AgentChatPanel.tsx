import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage } from "../lib/api";
import { live } from "../lib/live";

interface Bubble extends ChatMessage { key: number }

const QUICK = [
  "สถานะตอนนี้เป็นอย่างไร",
  "ทำไมยังไม่ BUY",
  "มี position อะไรบ้าง",
  "วันนี้เทรดไปกี่ครั้ง",
  "ช่วยวิเคราะห์ trade ที่แพ้",
  "หยุดเปิดออเดอร์ใหม่",
  "Resume Trading",
];

export default function AgentChatPanel({ compact = false }: { compact?: boolean }) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.chatHistory().then((r) =>
      setBubbles(r.messages.slice(-40).map((m, i) => ({ ...m, key: i })))
    ).catch(() => undefined);
    return live.subscribe((msg) => {
      if (msg.type === "CHAT_MESSAGE" && msg.payload?.reply) {
        setBubbles((b) => [...b.slice(-60), {
          ts: new Date().toISOString(), role: "agent", user: "agent",
          text: msg.payload.reply, intent: msg.payload.intent, key: Date.now(),
        }]);
      }
    });
  }, []);

  useEffect(() => {
    boxRef.current?.scrollTo(0, boxRef.current.scrollHeight);
  }, [bubbles]);

  async function send(msg: string) {
    const t = msg.trim();
    if (!t || busy) return;
    setBusy(true);
    setText("");
    setBubbles((b) => [...b, { ts: new Date().toISOString(), role: "user", user: "me", text: t, intent: null, key: Date.now() }]);
    try {
      const r = await api.sendChat(t);
      setBubbles((b) => [...b, { ts: new Date().toISOString(), role: "agent", user: "agent", text: r.reply, intent: r.intent, key: Date.now() + 1 }]);
    } catch (e) {
      setBubbles((b) => [...b, { ts: new Date().toISOString(), role: "agent", user: "agent", text: `❌ ${e}`, intent: null, key: Date.now() + 2 }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-box" ref={boxRef} style={{ height: compact ? 320 : undefined }}>
        {bubbles.length === 0 && (
          <p className="muted">พูดคุยกับ Trading Agent — ถามสถานะ, ขอวิเคราะห์, หรือสั่งจัดการ strategy</p>
        )}
        {bubbles.map((b) => (
          <div key={b.key} className={`bubble ${b.role === "user" ? "user" : "agent"}`}>
            {b.text}
            {b.intent && <div className="muted tiny">intent: {b.intent}</div>}
          </div>
        ))}
        {busy && <div className="bubble agent muted">…thinking</div>}
      </div>
      <div className="quick-cmds">
        {QUICK.map((q) => (
          <button key={q} className="chip" onClick={() => send(q)} disabled={busy}>{q}</button>
        ))}
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="พิมพ์ข้อความถึง Agent…" disabled={busy} />
        <button className="btn primary" type="submit" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
