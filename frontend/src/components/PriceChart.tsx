import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  LineStyle,
  createChart,
} from 'lightweight-charts';
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';

import type { ChartBar, ChartLevels } from '../types/api';

/* The chart renders times in the browser's timezone; the spec wants Amsterdam.
 * Shift each timestamp by (Amsterdam offset − browser offset) so the wall
 * times shown are always Amsterdam, whatever the visitor's zone is. */
function amsTimestamp(iso: string): UTCTimestamp {
  const d = new Date(iso);
  const ams = new Date(
    d.toLocaleString('en-US', { timeZone: 'Europe/Amsterdam' }),
  );
  const utc = new Date(d.toLocaleString('en-US', { timeZone: 'UTC' }));
  const amsOffset = ams.getTime() - utc.getTime();
  const browserOffset = -d.getTimezoneOffset() * 60_000;
  return ((d.getTime() + amsOffset - browserOffset) / 1000) as UTCTimestamp;
}

/* Vertical separators at the first bar of each session (design: Main). */
class SessionDividers implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private times: UTCTimestamp[] = [];
  private views: IPrimitivePaneView[] = [
    {
      zOrder: () => 'bottom',
      renderer: (): IPrimitivePaneRenderer => ({
        draw: (target) => {
          const chart = this.chart;
          if (!chart) return;
          target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
            ctx.strokeStyle = DIVIDER_COLOR;
            ctx.lineWidth = 1;
            for (const t of this.times) {
              const x = chart.timeScale().timeToCoordinate(t as Time);
              if (x === null) continue;
              ctx.beginPath();
              ctx.moveTo(x, 0);
              ctx.lineTo(x, mediaSize.height);
              ctx.stroke();
            }
          });
        },
      }),
    },
  ];

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
  }

  setTimes(times: UTCTimestamp[]): void {
    this.times = times;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }
}

const DIVIDER_COLOR = '#232A35'; // --sa-border
const UP = '#3DD68C'; // --sa-positive
const DOWN = '#FF6369'; // --sa-negative
const EMA20 = '#7FB8FF'; // --sa-chart-ema20
const EMA50 = '#C792EA'; // --sa-chart-ema50
const VWAP = '#F2B94B'; // --sa-chart-vwap
const ENTRY = '#C9CFD6'; // --sa-text-2
const TARGET = '#3DD68C';
const STOP = '#FF6369';
const GRID = '#1C222C'; // --sa-subtle
const TEXT = '#98A2AF'; // --sa-text-muted

export function PriceChart({
  bars,
  levels,
}: {
  bars: ChartBar[];
  levels: ChartLevels | null;
}) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el || bars.length === 0) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: TEXT,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      crosshair: { mode: 0 },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: GRID,
      },
      rightPriceScale: { borderColor: GRID },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    });
    candles.setData(
      bars.map((b) => ({
        time: amsTimestamp(b.time),
        open: Number(b.open),
        high: Number(b.high),
        low: Number(b.low),
        close: Number(b.close),
      })),
    );

    const line = (color: string): ISeriesApi<'Line'> =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

    const addLine = (
      pick: (b: ChartBar) => string | null,
      color: string,
    ) => {
      const data = bars
        .filter((b) => pick(b) !== null)
        .map((b) => ({ time: amsTimestamp(b.time), value: Number(pick(b)) }));
      if (data.length > 0) line(color).setData(data);
    };
    addLine((b) => b.vwap, VWAP);
    addLine((b) => b.ema20, EMA20);
    addLine((b) => b.ema50, EMA50);

    // Level lines: entry dashed, stop negative, target positive.
    const priceLine = (
      value: string | null | undefined,
      color: string,
      title: string,
      dashed = false,
    ) => {
      if (value == null) return;
      candles.createPriceLine({
        price: Number(value),
        color,
        lineWidth: 1,
        lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title,
      });
    };
    priceLine(levels?.entry, ENTRY, levels?.kind === 'position' ? 'entry' : 'limit', true);
    priceLine(levels?.stop, STOP, 'stop');
    priceLine(levels?.target, TARGET, 'target');

    // Session separators at the first bar of each session.
    const dividers = new SessionDividers();
    const starts: UTCTimestamp[] = [];
    let lastSession: string | null = null;
    for (const b of bars) {
      if (b.session !== lastSession) {
        starts.push(amsTimestamp(b.time));
        lastSession = b.session;
      }
    }
    dividers.setTimes(starts.slice(1));
    candles.attachPrimitive(dividers);

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, levels]);

  return (
    <div
      ref={host}
      data-testid="price-chart"
      style={{ width: '100%', height: '340px' }}
    />
  );
}
