import { useEffect, useRef } from 'react';
import { LineSeries, LineStyle, createChart } from 'lightweight-charts';
import type { Time } from 'lightweight-charts';

export interface StrategySeries {
  name: string;
  colour: string;
  points: { date: string; pct: number }[];
  dashed?: boolean;
}

const GRID = '#1C222C'; // --sa-subtle
const TEXT = '#98A2AF'; // --sa-text-muted

function businessDay(time: Time): string {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  const d = time as { year: number; month: number; day: number };
  return `${d.year}-${String(d.month).padStart(2, '0')}-${String(d.day).padStart(2, '0')}`;
}

/** Equity since the run started as % of starting equity, one line per
 * strategy plus SPY dashed (design: Strategies). One point per session. */
export function StrategyEquityChart({ series }: { series: StrategySeries[] }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el || series.every((s) => s.points.length === 0)) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: TEXT,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: GRID } },
      timeScale: {
        borderColor: GRID,
        fixLeftEdge: true,
        tickMarkFormatter: (time: Time) =>
          new Date(`${businessDay(time)}T12:00:00Z`).toLocaleDateString('en-GB', {
            day: 'numeric',
            month: 'short',
            timeZone: 'UTC',
          }),
      },
      rightPriceScale: { borderColor: GRID },
      localization: {
        priceFormatter: (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%`,
      },
    });
    for (const s of series) {
      if (s.points.length === 0) continue;
      const line = chart.addSeries(LineSeries, {
        color: s.colour,
        lineWidth: 2,
        lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid,
        pointMarkersVisible: s.points.length < 3,
        priceLineVisible: false,
        lastValueVisible: false,
        title: '',
      });
      line.setData(s.points.map((p) => ({ time: p.date, value: p.pct })));
    }
    const raf = requestAnimationFrame(() => chart.timeScale().fitContent());
    return () => {
      cancelAnimationFrame(raf);
      chart.remove();
    };
  }, [series]);

  return <div ref={host} data-testid="strategies-chart" style={{ width: '100%', height: '280px' }} />;
}
