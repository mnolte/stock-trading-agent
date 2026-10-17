import { useEffect, useRef } from 'react';
import { LineSeries, LineStyle, createChart } from 'lightweight-charts';
import type { IChartApi } from 'lightweight-charts';
import type { Time } from 'lightweight-charts';

export interface EquityCurvePoint {
  /** Session date in New York, YYYY-MM-DD. */
  date: string;
  equity: number;
  benchmark: number | null;
}

const AGENT = '#F2B94B'; // --sa-chart-vwap (agent accent)
const BENCH = '#98A2AF'; // --sa-text-muted
const GRID = '#1C222C'; // --sa-subtle
const TEXT = '#98A2AF';

function businessDay(time: Time): string {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') {
    return new Date(time * 1000).toISOString().slice(0, 10);
  }
  const d = time as { year: number; month: number; day: number };
  return `${d.year}-${String(d.month).padStart(2, '0')}-${String(d.day).padStart(2, '0')}`;
}

/** Equity vs buy-and-hold benchmark, one point per session (design:
 * Performance). Daily data, so the x axis uses session dates — weekends and
 * holidays simply do not appear. */
export function EquityCurveChart({
  points,
  currency,
}: {
  points: EquityCurvePoint[];
  currency: 'USD' | 'EUR';
}) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

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
          const d = new Date(`${day}T12:00:00Z`);
          return d.toLocaleDateString('en-GB', {
            day: 'numeric',
            month: 'short',
            timeZone: 'UTC',
          });
        },
      },
      rightPriceScale: { borderColor: GRID },
      localization: {
        priceFormatter: (price: number) =>
          `${currency === 'EUR' ? '€' : '$'}${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
      },
    });
    chartRef.current = chart;

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

    // autoSize lays the canvas out asynchronously; fit after the first frame
    // so the series actually spans the width.
    const raf = requestAnimationFrame(() => chart.timeScale().fitContent());
    return () => {
      cancelAnimationFrame(raf);
      chart.remove();
      chartRef.current = null;
    };
  }, [points, currency]);

  return (
    <div
      ref={host}
      data-testid="equity-chart"
      style={{ width: '100%', height: '300px' }}
    />
  );
}
