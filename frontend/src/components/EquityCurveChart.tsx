import { useEffect, useRef, useState } from 'react';
import { LineSeries, LineStyle, createChart } from 'lightweight-charts';
import type { Time } from 'lightweight-charts';
import { fmtDayMonthIn } from '../format';

export interface EquityCurvePoint {
  /** Session date in New York, YYYY-MM-DD. */
  date: string;
  equity: number;
  benchmark: number | null;
}

/** Item 076: one agent trade's exit, a ring on the curve. */
export interface CurveExit {
  date: string;
  symbol: string;
  r: string | null;
  won: boolean;
}

/** Item 076: the deepest drawdown's peak → low span, shaded on the curve. */
export interface CurveDrawdown {
  peak_date: string;
  low_date: string;
  pct: string;
}

interface ExitMark {
  key: string;
  x: number;
  y: number;
  title: string;
  won: boolean;
}

const AGENT = '#F2B94B'; // --sa-chart-vwap (agent accent)
const BENCH = '#98A2AF'; // --sa-text-muted
const GRID = '#1C222C'; // --sa-subtle
const TEXT = '#98A2AF';
const UP = '#3DD68C';
const DOWN = '#FF6369';

function businessDay(time: Time): string {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') {
    return new Date(time * 1000).toISOString().slice(0, 10);
  }
  const d = time as { year: number; month: number; day: number };
  return `${d.year}-${String(d.month).padStart(2, '0')}-${String(d.day).padStart(2, '0')}`;
}

function signedR(r: string | null): string {
  if (r == null) return '';
  const n = Number(r);
  if (n > 0) return `+${n.toFixed(2)}R`;
  if (n < 0) return `−${Math.abs(n).toFixed(2)}R`;
  return `${n.toFixed(2)}R`;
}

/** Equity vs buy-and-hold benchmark, one point per session (design:
 * Performance). Daily data, so the x axis uses session dates — weekends and
 * holidays simply do not appear. Item 076: trade exits are rings on the
 * curve and the deepest drawdown is shaded peak → low. */
export function EquityCurveChart({
  points,
  currency,
  exits,
  drawdown,
}: {
  points: EquityCurvePoint[];
  currency: 'USD' | 'EUR';
  exits?: CurveExit[];
  drawdown?: CurveDrawdown | null;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [marks, setMarks] = useState<ExitMark[]>([]);
  const [band, setBand] = useState<{ left: number; width: number; bottom: number } | null>(null);

  useEffect(() => {
    const el = host.current;
    if (!el || points.length === 0) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: TEXT,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: GRID, visible: false },
        horzLines: { color: GRID },
      },
      timeScale: {
        borderColor: GRID,
        fixLeftEdge: true,
        tickMarkFormatter: (time: Time) => {
          const day = businessDay(time);
          if (day === points[0].date) return 'Start';
          return fmtDayMonthIn(day);
        },
      },
      rightPriceScale: { borderColor: GRID },
      localization: {
        priceFormatter: (price: number) =>
          `${currency === 'EUR' ? '€' : '$'}${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
      },
    });

    const bench = chart.addSeries(LineSeries, {
      color: BENCH,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      pointMarkersVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    });
    bench.setData(
      points
        .filter((p) => p.benchmark !== null)
        .map((p) => ({ time: p.date, value: p.benchmark as number })),
    );

    const equity = chart.addSeries(LineSeries, {
      color: AGENT,
      lineWidth: 2,
      pointMarkersVisible: true,
      pointMarkersRadius: 3,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
    });
    equity.setData(points.map((p) => ({ time: p.date, value: p.equity })));

    const place = () => {
      const ts = chart.timeScale();
      const byDate = new Map(points.map((p) => [p.date, p]));
      const nextMarks: ExitMark[] = [];
      for (const e of exits ?? []) {
        const p = byDate.get(e.date);
        if (!p) continue;
        const x = ts.timeToCoordinate(p.date as Time);
        const y = equity.priceToCoordinate(p.equity);
        if (x == null || y == null) continue;
        const r = signedR(e.r);
        nextMarks.push({
          key: `${e.date}-${e.symbol}`,
          x,
          y,
          won: e.won,
          title: `${e.symbol} · ${r || (e.won ? 'won' : 'lost')}`,
        });
      }
      setMarks(nextMarks);

      let nextBand: { left: number; width: number; bottom: number } | null = null;
      if (drawdown && Number(drawdown.pct) > 0) {
        const x1 = ts.timeToCoordinate(drawdown.peak_date as Time);
        const x2 = ts.timeToCoordinate(drawdown.low_date as Time);
        if (x1 != null && x2 != null && x1 !== x2) {
          const axisH = ts.height();
          nextBand = {
            left: Math.min(x1, x2),
            width: Math.abs(x2 - x1),
            bottom: Number.isFinite(axisH) ? axisH : 28,
          };
        }
      }
      setBand(nextBand);
    };

    // autoSize lays the canvas out asynchronously; fit after the first
    // frame so the series actually spans the width, then place overlays.
    const raf = requestAnimationFrame(() => {
      chart.timeScale().fitContent();
      place();
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange(place);
    return () => {
      cancelAnimationFrame(raf);
      chart.remove();
    };
  }, [points, currency, exits, drawdown]);

  const ddLabel =
    drawdown && Number(drawdown.pct) > 0
      ? `Deepest drawdown ${Number(drawdown.pct).toFixed(2)}%: ${fmtDayMonthIn(drawdown.peak_date)} to ${fmtDayMonthIn(drawdown.low_date)}`
      : null;

  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <div
        ref={host}
        data-testid="equity-chart"
        style={{ width: '100%', height: '300px' }}
      />
      <div aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
        {band && (
          <div
            title={ddLabel ?? undefined}
            style={{
              position: 'absolute',
              top: 0,
              bottom: band.bottom,
              left: band.left,
              width: band.width,
              background: 'rgba(255,99,105,0.08)',
              borderLeft: '1px dashed rgba(255,99,105,0.4)',
              borderRight: '1px dashed rgba(255,99,105,0.4)',
              pointerEvents: 'auto',
            }}
          />
        )}
        {marks.map((m) => (
          <div
            key={m.key}
            title={m.title}
            style={{
              position: 'absolute',
              left: m.x - 7,
              top: m.y - 7,
              width: 14,
              height: 14,
              borderRadius: '50%',
              boxSizing: 'border-box',
              border: `2px solid ${m.won ? UP : DOWN}`,
              background: 'transparent',
              pointerEvents: 'auto',
            }}
          />
        ))}
      </div>
    </div>
  );
}
