import { getIdToken } from "./firebase";

// API base: same-origin /api โดยดีฟอลต์ (localhost dev), override ได้ผ่าน VITE_API_BASE
// เมื่อโฮสต์ dashboard ที่เป็น static site แล้วชี้มาที่ backend สาธารณะ
const BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getIdToken();
  const res = await fetch(BASE + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export const api = {
  systemStatus: () => request<SystemStatus>("/system/status"),
  account: () => request<AccountResponse>("/account"),
  trades: (limit = 100) => request<TradesResponse>(`/trades?limit=${limit}`),
  statistics: () => request<StatisticsResponse>("/statistics"),
  strategies: () => request<StrategiesResponse>("/strategies"),
  decisions: (limit = 50) => request<DecisionsResponse>(`/decisions?limit=${limit}`),
  audit: (limit = 100) => request<AuditResponse>(`/audit?limit=${limit}`),
  chatHistory: () => request<ChatHistoryResponse>("/chat/history"),
  sendChat: (text: string) =>
    request<ChatResponse>("/chat", { method: "POST", body: JSON.stringify({ text }) }),
  pause: (paused: boolean) =>
    request<{ ok: boolean }>("/control/pause", { method: "POST", body: JSON.stringify({ paused }) }),
  emergencyStop: (on: boolean) =>
    request<{ ok: boolean }>("/control/emergency-stop", { method: "POST", body: JSON.stringify({ on }) }),
  backtest: (version: string) => request<BacktestResult>(`/backtest/${version}`, { method: "POST" }),
  activateStrategy: (version: string) =>
    request<{ ok: boolean }>(`/strategies/${version}/activate`, { method: "POST" }),
  market: (symbol: string) => request<MarketSnapshot>(`/market/${symbol}`),
  candles: (symbol: string, timeframe: string, count = 200) =>
    request<CandlesResponse>(`/candles/${symbol}?timeframe=${timeframe}&count=${count}`),
  tradeDetail: (id: number) => request<TradeDetail>(`/trade/${id}/detail`),
  equityCurve: (days = 30) => request<EquityCurve>(`/analytics/equity-curve?days=${days}`),
  learningSummary: () => request<LearningSummary>("/learning/summary"),
  learningReview: () => request<Record<string, unknown>>("/learning/review", { method: "POST" }),
  v1State: () => request<V1State>("/v1/state"),
  closeAll: (confirm = true) =>
    request<CloseAllResult>("/control/close-all", { method: "POST", body: JSON.stringify({ confirm }) }),
  providers: () => request<ProvidersResponse>("/providers"),
  selectProvider: (provider: string) =>
    request<ProviderSelectResult>("/providers/select", { method: "POST", body: JSON.stringify({ provider }) }),
  mt5Reconnect: () => request<MT5ReconnectResult>("/mt5/reconnect", { method: "POST", body: JSON.stringify({}) }),
  telegramStatus: () => request<TelegramStatus>("/telegram/status"),
  telegramTest: (token?: string, chatId?: string, text?: string) =>
    request<{ ok: boolean }>("/telegram/test", { method: "POST", body: JSON.stringify({ token, chat_id: chatId, text }) }),
};

// ---- types ----
export interface SystemStatus {
  agent: { running: boolean; cycles: number; last_beat: string; mode: string; paused: boolean; emergency_stop: boolean; active_strategy: string | null };
  mt5: { connected: boolean; mode: string; error?: string; terminal?: { name?: string; trade_allowed?: boolean } };
  mcp: { mode: string; connected?: boolean };
  trading_mode: string;
  live_trading: boolean;
  symbols: string[];
}
export interface AccountResponse {
  account: { login: string; name: string; currency: string; balance: number; equity: number; margin_used: number; leverage: number; server: string; mode: string };
  positions: Position[];
}
export interface Position {
  ticket: string; symbol: string; side: string; lot: number; entry_price: number;
  current_price: number; pnl: number; sl: number; tp: number; opened_at: string; strategy_version?: string;
}
export interface Trade {
  id: number; ts_open: string; ts_close: string | null; symbol: string; side: string; lot: number;
  entry_price: number | null; exit_price: number | null; sl: number | null; tp: number | null;
  pnl: number | null; exit_reason: string | null; strategy_version: string; mode: string; ticket: string;
}
export interface TradesResponse { trades: Trade[]; open: Trade[] }
export interface StatisticsResponse {
  summary: { trades?: number; wins?: number; losses?: number; win_rate?: number; total_pnl?: number; avg_win?: number; avg_loss?: number; profit_factor?: number | null; max_drawdown?: number; expectancy?: number; by_strategy?: Record<string, { trades: number; wins: number; pnl: number }>; note?: string };
  today: { date: string; trades: number; wins: number; pnl: number };
}
export interface StrategyRow {
  version: string; base_version: string | null; name: string; status: string; rules_json: string;
  created_by: string; created_at: string; approved_by: string | null; origin: string;
}
export interface StrategiesResponse { strategies: StrategyRow[]; active: string; proposals: Array<{ id: number; proposed_version: string; base_version: string; status: string; rationale: string; created_by: string }> }
export interface DecisionRow { id: number; ts: string; symbol: string; decision: string; signal: string | null; strategy_version: string; risk_result: string | null; reason: string; detail_json: string | null }
export interface DecisionsResponse { decisions: DecisionRow[] }
export interface AuditEvent { id: number; ts: string; user: string; agent: string; action: string; result: string; strategy_version: string; payload_json?: string }
export interface AuditResponse { events: AuditEvent[] }
export interface ChatMessage { ts: string; role: string; user: string | null; text: string; intent: string | null }
export interface ChatHistoryResponse { messages: ChatMessage[] }
export interface ChatResponse { reply: string; intent: string }
export interface BacktestResult {
  strategy_version: string; baseline_version: string;
  baseline: { metrics: BacktestMetrics };
  proposal: { metrics: BacktestMetrics };
}
export interface BacktestMetrics { trades: number; win_rate: number; total_pnl: number; final_equity: number; max_drawdown: number; avg_win: number; avg_loss: number }
export interface MarketSnapshot {
  symbol: string;
  price: { bid: number; ask: number; spread_points: number; time: string };
  indicators: { rsi14: number | null; ema9: number | null; ema21: number | null; atr14: number | null };
  last_candle: { open: number; high: number; low: number; close: number } | null;
}
export interface Candle { time: string; open: number; high: number; low: number; close: number; tick_volume: number }
export interface CandlesResponse {
  symbol: string; timeframe: string; candles: Candle[];
  indicators: Record<string, (number | null)[] | null>;
  current_price: { bid: number; ask: number; spread_points: number; time: string };
  indicators_now: { rsi14: number | null; ema9: number | null; ema21: number | null; atr14: number | null };
}
export interface TradeDetail {
  trade: Trade;
  decision: DecisionRow | null;
  open_event: AuditEvent | null;
  close_event: AuditEvent | null;
}
export interface EquityPoint { ts: string; equity: number; pnl: number; drawdown: number }
export interface EquityCurve { start: number; points: EquityPoint[]; max_drawdown: number; current_equity: number }
export interface LearningSummary {
  summary: StatisticsResponse["summary"];
  per_strategy: Array<{ version: string; trades: number; wins: number; losses: number; pnl: number }>;
  recent_losers: Trade[];
  active: string;
}

// ---- Strategy V1 (Stochastic Curve + Chart Context) ----
export interface PriceActionContext {
  market_structure: { trend: string; state: string; sequence?: string[]; structure_strength?: number };
  price_location: { zone: string; quality: number };
  candlestick: { pattern: string; behavior: string; quality: number };
  breakout: { state: string; confirmed: boolean };
  retest: { state: string; confirmed: boolean };
  context: string;
  support_score: number;
  contradiction_score: number;
  uncertainty_score: number;
  reason_codes: string[];
  summary: string;
  knowledge_id?: string;
}
export interface V1State {
  strategy_id: string;
  strategy_version: string;
  timeframe: string;
  autonomous: boolean;
  stochastic: { k: number | null; d: number | null; previous_k: number | null; previous_d: number | null; curve: string; reason: string };
  chart_context: { decision?: string; trend?: string; structure?: string; price_location?: string; candle_context?: string; signal_quality?: number; summary?: string; source?: string; price_action?: PriceActionContext };
  price_action_snapshot: (PriceActionContext & { probe?: boolean; probe_side?: string }) | null;
  last_decision: { ts: string; symbol: string; decision: string; risk_result: string | null; reason: string; detail: Record<string, unknown> } | null;
  error: string;
}
export interface CloseAllResult { ok: boolean; closed: Array<{ ticket: string; symbol: string }>; failed: Array<{ ticket: string; error: string }> }

export interface ProviderInfo {
  id: string; name: string;
  symbol_map: Record<string, string>;
  notes: string; broker_server_hint: string; login_url: string; builtin: boolean;
}
export interface ProvidersResponse { active: string; providers: ProviderInfo[]; mt5_mode: string }
export interface MT5StatusInfo { connected: boolean; mode: string; error?: string; terminal?: { name?: string; build?: number; trade_allowed?: boolean } }
export interface ProviderSelectResult { ok: boolean; active: string; provider: ProviderInfo; mt5: MT5StatusInfo }
export interface MT5ReconnectResult { ok: boolean; mt5?: MT5StatusInfo; error?: string }
export interface TelegramStatus { configured: boolean; token_set: boolean; chat_id_set: boolean }
