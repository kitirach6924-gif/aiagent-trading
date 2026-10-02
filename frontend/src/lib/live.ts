import { useEffect, useRef, useState } from "react";

// ---- split realtime stores: price ticks must not re-render trade/event views ----

export interface Tick {
  bid: number;
  ask: number;
  spread: number;
  ts: string;
  stale?: boolean;
}

export interface AccountSnapshot {
  account: { login: string; name: string; currency: string; balance: number; equity: number; margin_used: number; leverage: number; server: string; mode: string };
  positions: Array<{ ticket: string; symbol: string; side: string; lot: number; entry_price: number; current_price: number; pnl: number; sl: number; tp: number; opened_at: string; strategy_version?: string }>;
  ts: string;
}

export interface Heartbeat {
  agent: { running: boolean; cycles: number; last_beat: string; paused: boolean; emergency_stop: boolean; active_strategy: string | null };
  mt5: { connected: boolean; mode: string; terminal?: { name?: string } };
  mcp: { mode: string; connected?: boolean };
  trading_mode: string;
  live_trading: boolean;
  symbols: string[];
  data_source?: string;
  ts: string;
}

export interface LiveEvent {
  action: string;
  ts: string;
  result: string;
  agent: string;
  strategy_version?: string;
  payload?: unknown;
  seq: number;
}

type Listener = (msg: any) => void;

class LiveClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private backoff = 1000;
  private closed = false;
  connected = false;

  connect() {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    this.closed = false;
    // WS base: same-origin โดยดีฟอลต์; บน VPS ให้ตั้ง VITE_WS_BASE=wss://<host> (ตัด /ws ท้ายออก)
    const wsBase = (import.meta.env.VITE_WS_BASE as string | undefined) ??
      `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
    const ws = new WebSocket(`${wsBase}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.backoff = 1000;
      this.emit({ type: "_conn", connected: true });
    };
    ws.onclose = () => {
      this.connected = false;
      this.emit({ type: "_conn", connected: false });
      if (!this.closed) {
        setTimeout(() => this.connect(), this.backoff);
        this.backoff = Math.min(this.backoff * 2, 15000);
      }
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (m) => {
      try {
        this.emit(JSON.parse(m.data));
      } catch {
        /* ignore */
      }
    };
  }

  private emit(msg: any) {
    this.listeners.forEach((l) => l(msg));
  }

  subscribe(l: Listener): () => void {
    this.listeners.add(l);
    this.connect();
    return () => this.listeners.delete(l);
  }
}

export const live = new LiveClient();

let seq = 0;

/** Subscribe to one message type with automatic re-render. */
export function useLive<T>(type: string, transform?: (msg: any) => T): T | null {
  const [value, setValue] = useState<T | null>(null);
  const transformRef = useRef(transform);
  transformRef.current = transform;
  useEffect(() => {
    return live.subscribe((msg) => {
      if (msg.type === type) {
        setValue(transformRef.current ? transformRef.current(msg) : msg);
      }
    });
  }, [type]);
  return value;
}

export function useConnection(): boolean {
  const [connected, setConnected] = useState(live.connected);
  useEffect(() => live.subscribe((m) => {
    if (m.type === "_conn") setConnected(m.connected);
  }), []);
  return connected;
}

export function useEvents(max = 100): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  useEffect(() => {
    return live.subscribe((msg) => {
      if (msg.type === "EVENT") {
        setEvents((prev) => [{ ...msg, seq: ++seq }, ...prev].slice(0, max));
      }
    });
  }, [max]);
  return events;
}

/** Ticks keyed by symbol, with staleness flag (>5s old). */
export function useTicks(): Record<string, Tick> {
  const [ticks, setTicks] = useState<Record<string, Tick>>({});
  useEffect(() => {
    const t = setInterval(() => {
      setTicks((prev) => {
        const now = Date.now();
        const next: Record<string, Tick> = {};
        for (const [k, v] of Object.entries(prev)) {
          const age = now - new Date(v.ts).getTime();
          next[k] = { ...v, stale: age > 5000 };
        }
        return next;
      });
    }, 2000);
    const unsub = live.subscribe((msg) => {
      if (msg.type === "MARKET_TICK" && msg.prices) {
        setTicks((prev) => {
          const next = { ...prev };
          for (const [sym, p] of Object.entries<any>(msg.prices)) {
            if (p.bid) next[sym] = { bid: p.bid, ask: p.ask, spread: p.spread, ts: msg.ts, stale: false };
          }
          return next;
        });
      }
    });
    return () => {
      clearInterval(t);
      unsub();
    };
  }, []);
  return ticks;
}
