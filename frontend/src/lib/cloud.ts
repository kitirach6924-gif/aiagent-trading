/** Cloud View: อ่านสถานะจาก Firestore ตรง (fallback เมื่อเรียก /api ไม่ได้ เช่น
 *  dashboard สาธารณะที่ backend อยู่เครื่อง local หรือ Cloud Run ยังไม่พร้อม)
 *  อ่านอย่างเดียว — rules ฝั่ง Firestore อนุญาตเฉพาะผู้ที่ล็อกอิน */
import { onSnapshot } from "firebase/firestore";
import { doc, collection, query, orderBy, limit } from "firebase/firestore";
import { firestore } from "./firebase";

export interface CloudStatus {
  runner?: string;
  agent?: { running: boolean; cycles: number; last_beat: string; paused: boolean; emergency_stop: boolean; active_strategy: string | null };
  trading_mode?: string;
  live_trading?: boolean;
  symbols?: string[];
  positions?: Array<{ ticket: string; symbol: string; side: string; lot: number; entry_price: number; current_price: number; pnl: number }>;
  account?: { balance: number; equity: number; currency: string; server: string; mode: string };
  updated_at?: number;
}

export interface CloudTrade {
  id: string;
  symbol?: string;
  side?: string;
  lot?: number;
  entry?: number;
  exit_price?: number | null;
  pnl?: number | null;
  ts?: string;
  ts_close?: string | null;
  status?: string;
}

export function watchCloudStatus(cb: (s: CloudStatus | null) => void): () => void {
  if (!firestore) { cb(null); return () => undefined; }
  return onSnapshot(doc(firestore, "system_status", "current"), (snap) => {
    cb(snap.exists() ? (snap.data() as CloudStatus) : null);
  }, () => cb(null));
}

export function watchCloudTrades(cb: (t: CloudTrade[]) => void): () => void {
  if (!firestore) { cb([]); return () => undefined; }
  const q = query(collection(firestore, "trades"), orderBy("ts", "desc"), limit(20));
  return onSnapshot(q, (snap) => {
    cb(snap.docs.map((d) => ({ id: d.id, ...(d.data() as Omit<CloudTrade, "id">) })));
  }, () => cb([]));
}
