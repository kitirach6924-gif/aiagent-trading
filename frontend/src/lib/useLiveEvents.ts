import { useEffect, useRef, useState } from "react";

export interface LiveEvent {
  type: string;
  ts: string;
  payload: unknown;
  result: string;
  agent: string;
  seq: number;
}

let seqCounter = 0;

export function useLiveEvents(max = 50): { events: LiveEvent[]; connected: boolean } {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const wsBase = (import.meta.env.VITE_WS_BASE as string | undefined) ??
      `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
    const ws = new WebSocket(`${wsBase}/ws`);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data) as Omit<LiveEvent, "seq">;
        if (data.type === "pong") return;
        setEvents((prev) => [{ ...data, seq: ++seqCounter }, ...prev].slice(0, max));
      } catch {
        /* ignore */
      }
    };
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);
    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, [max]);

  return { events, connected };
}
