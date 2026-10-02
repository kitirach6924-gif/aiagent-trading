import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type SeriesMarker,
  Time,
} from "lightweight-charts";
import { api, type Candle, type TradeDetail } from "../lib/api";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

interface Props {
  symbol: string;
  tick?: { bid: number; ask: number; stale?: boolean } | null;
  markers?: SeriesMarker<Time>[];
  sltp?: { sl?: number | null; tp?: number | null; entry?: number | null } | null;
  onMarkerClick?: (detail: TradeDetail) => void;
}

export default function LiveChart({ symbol, tick, markers, sltp, onMarkerClick }: Props) {
  const [timeframe, setTimeframe] = useState("M15");
  const [indicators, setIndicators] = useState<Record<string, (number | null)[] | null>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const chartRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lineSeries = useRef<Record<string, ISeriesApi<"Line">>>({});

  // init chart once
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = createChart(chartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0e1117" },
        textColor: "#9aa4b2",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1c2230" },
        horzLines: { color: "#1c2230" },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: "#262d3b" },
      timeScale: { borderColor: "#262d3b", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    chartApi.current = chart;
    candleSeries.current = chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderUpColor: "#26a69a", borderDownColor: "#ef5350",
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    volumeSeries.current = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: "#2d3a4d",
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    chart.subscribeClick((param: any) => {
      const data = param.seriesData.get(candleSeries.current!) as any;
      if (data?.tradeId && onMarkerClick) {
        api.tradeDetail(data.tradeId).then(onMarkerClick).catch(() => undefined);
      }
    });

    return () => chart.remove();
  }, [onMarkerClick]);

  // load candles + indicator series from strategy config
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .candles(symbol, timeframe, 200)
      .then((res) => {
        if (cancelled) return;
        const bars: CandlestickData[] = res.candles.map((c: Candle) => ({
          time: (new Date(c.time).getTime() / 1000) as unknown as Time,
          open: c.open, high: c.high, low: c.low, close: c.close,
        }));
        candleSeries.current?.setData(bars);
        volumeSeries.current?.setData(
          res.candles.map((c: Candle) => ({
            time: (new Date(c.time).getTime() / 1000) as unknown as Time,
            value: c.tick_volume,
            color: c.close >= c.open ? "#1d4d44" : "#5c2b2b",
          }))
        );
        setIndicators(res.indicators ?? {});
        setVisible((prev) => {
          const next: Record<string, boolean> = {};
          Object.keys(res.indicators ?? {}).forEach((k) => {
            next[k] = prev[k] ?? k.startsWith("EMA");
          });
          return next;
        });
        chartApi.current?.timeScale().fitContent();
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof SyntaxError ? "โหลดกราฟไม่ได้ — API ไม่พร้อม (ดูได้ที่ localhost)" : String(e));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  // sync indicator line series
  useEffect(() => {
    const chart = chartApi.current;
    if (!chart) return;
    for (const [name, values] of Object.entries(indicators)) {
      if (!values) continue;
      if (!lineSeries.current[name]) {
        const colors: Record<string, string> = {
          RSI: "#c792ea", ATR: "#82aaff",
        };
        const color = name.startsWith("EMA")
          ? name === "EMA9" ? "#ffd54f" : "#4dd0e1"
          : colors[name.replace(/\d+/g, "")] ?? "#888";
        lineSeries.current[name] = chart.addLineSeries({
          color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
          title: name,
        });
      }
      const candles = candleSeries.current?.data() as CandlestickData[] | undefined;
      if (candles?.length) {
        const startIdx = candles.length - values.length;
        const line: LineData[] = [];
        values.forEach((v, i) => {
          if (v == null) return;
          const t = candles[Math.max(0, startIdx + i)]?.time;
          if (t) line.push({ time: t as Time, value: v });
        });
        lineSeries.current[name].setData(line);
        lineSeries.current[name].applyOptions({ visible: visible[name] ?? false });
      }
    }
  }, [indicators, visible]);

  // live tick → update last candle close via bid
  useEffect(() => {
    if (!tick || tick.stale) return;
    const price = tick.bid;
    try {
      const data = candleSeries.current?.data?.() as CandlestickData[] | undefined;
      if (!data?.length) return;
      const last = data[data.length - 1] as any;
      candleSeries.current!.update({
        time: last.time, open: last.open, high: Math.max(last.high, price),
        low: Math.min(last.low, price), close: price,
      });
    } catch {
      /* ignore */
    }
  }, [tick]);

  // markers
  useEffect(() => {
    candleSeries.current?.setMarkers(markers ?? []);
  }, [markers]);

  // SL/TP price lines
  useEffect(() => {
    if (!candleSeries.current) return;
    if (!sltp?.sl && !sltp?.tp) return;
    const s = candleSeries.current;
    const lines: any[] = [];
    if (sltp.entry) lines.push(s.createPriceLine({ price: sltp.entry, color: "#58a6ff", lineWidth: 1, title: "Entry" }));
    if (sltp.sl) lines.push(s.createPriceLine({ price: sltp.sl, color: "#ef5350", lineWidth: 1, lineStyle: 2, title: "SL" }));
    if (sltp.tp) lines.push(s.createPriceLine({ price: sltp.tp, color: "#3fb950", lineWidth: 1, lineStyle: 2, title: "TP" }));
    return () => lines.forEach((l) => { try { s.removePriceLine(l); } catch { /* ignore */ } });
  }, [sltp?.sl, sltp?.tp, sltp?.entry]);

  return (
    <div className="chart-wrap">
      <div className="chart-toolbar">
        <span className="chart-symbol">{symbol}</span>
        {tick && (
          <span className={`chart-price ${tick.stale ? "stale" : ""}`}>
            {tick.bid.toFixed(2)} {tick.stale && "· STALE"}
          </span>
        )}
        <div className="tf-group">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} className={`tf-btn ${tf === timeframe ? "active" : ""}`} onClick={() => setTimeframe(tf)}>
              {tf}
            </button>
          ))}
        </div>
        <div className="ind-group">
          {Object.keys(indicators).map((k) => (
            <button
              key={k}
              className={`ind-btn ${visible[k] ? "active" : ""}`}
              onClick={() => setVisible((v) => ({ ...v, [k]: !v[k] }))}
            >
              {k}
            </button>
          ))}
        </div>
        {loading && <span className="muted small">loading…</span>}
        {error && <span className="error small">{error}</span>}
      </div>
      <div ref={chartRef} className="chart-container" />
    </div>
  );
}
