import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage } from "../lib/api";

interface Bubble extends ChatMessage { key: number }

const SUGGESTIONS = [
  "สถานะตลาดตอนนี้เป็นอย่างไร",
  "วันนี้เทรดไปกี่ครั้ง",
  "ช่วยวิเคราะห์ trade ที่แพ้",
  "เพิ่มกฎว่า spread สูงให้หยุดเทรด",
  "ทดสอบ v1.1",
  "หยุดเปิดออเดอร์ใหม่",
  "Resume Trading",
];

export default function AgentChat() {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.chatHistory().then((r) =>
      setBubbles(r.messages.map((m, i) => ({ ...m, key: i })))
    ).catch(() => undefined);
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
    <div className="card">
      <div className="chat-box" ref={boxRef}>
        {bubbles.length === 0 && (
          <p className="muted">พูดคุยกับ Trading Agent ได้เลย — ถามสถานะ, ขอวิเคราะห์, หรือสั่งจัดการ strategy</p>
        )}
        {bubbles.map((b) => (
          <div key={b.key} className={`bubble ${b.role === "user" ? "user" : "agent"}`}>
            {b.text}
            {b.intent && <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>intent: {b.intent}</div>}
          </div>
        ))}
        {busy && <div className="bubble agent muted">…thinking</div>}
      </div>
      <div className="controls">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="btn small" onClick={() => send(s)} disabled={busy}>{s}</button>
        ))}
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="พิมพ์ข้อความถึง Trading Agent…"
          disabled={busy}
        />
        <button className="btn primary" type="submit" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
