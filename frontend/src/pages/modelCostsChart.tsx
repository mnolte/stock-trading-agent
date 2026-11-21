import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

import { useMediaQuery } from '../hooks/useMediaQuery';
import {
  PLOT_HEIGHT,
  bucketDates,
  fmtUsd,
  labelPitch,
  sessionSentence,
  spanLabel,
  thinnedLabels,
} from './modelCostsFormat';
import type { SessionBucket, SessionSummary, SessionUnit } from './modelCostsFormat';

/** Insights → "Cost per session" chart (item 112, docs/SPEC.md →
 * Model costs → Cost per session; design/screens/Insights.dc.html §6).
 * Own full-width card under By model; at most 31 bars (the backend's
 * bucket_unit picks session/week/month/quarter), bars never wider than
 * 36 px, labels thinned to one per 36 px, click or tap a bar for its
 * amounts, arrow keys step through them. Types and pure helpers live
 * in modelCostsFormat.ts. */

const BAR_MAX_WIDTH = 36;
const BAR_GAP = 6;
const BAR_GAP_PHONE = 2;
const PHONE_QUERY = '(max-width: 599px)';

/** Stack colours from design/screens/Insights.dc.html (bottom → top:
 * live, shadows, reviews, Ask). */
const STACK = [
  { key: 'live', label: 'Live decisions', detail: 'live decisions', color: '#7FB8FF' },
  { key: 'shadows', label: 'Shadows', detail: 'shadows', color: '#B48CFF' },
  { key: 'reviews', label: 'Reviews', detail: 'reviews', color: '#3DD68C' },
  { key: 'ask', label: 'Ask', detail: 'Ask', color: '#F2B94B' },
] as const;

function bucketTotal(row: SessionBucket): number {
  return STACK.reduce((n, s) => n + Number(row[s.key]), 0);
}

function highestIndex(rows: SessionBucket[]): number {
  let best = 0;
  let bestTotal = -1;
  rows.forEach((r, i) => {
    const t = bucketTotal(r);
    if (t > bestTotal) {
      bestTotal = t;
      best = i;
    }
  });
  return best;
}

export function SessionChart({
  rows,
  unit,
  summary,
}: {
  rows: SessionBucket[];
  unit: SessionUnit;
  summary: SessionSummary | null;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  const gap = phone ? BAR_GAP_PHONE : BAR_GAP;
  const [selKey, setSelKey] = useState<string | null>(null);
  const [plotWidth, setPlotWidth] = useState(0);
  const barsRef = useRef<HTMLDivElement>(null);
  const btnRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const el = barsRef.current;
    if (!el) return;
    const update = () => setPlotWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [rows.length]);

  const totals = useMemo(() => rows.map(bucketTotal), [rows]);
  const maxTotal = Math.max(...totals, 0);
  const rawTop = Math.max(maxTotal, 0.0001) * 1.15;
  const step = rawTop > 1 ? 0.5 : rawTop > 0.2 ? 0.1 : rawTop > 0.05 ? 0.02 : 0.01;
  const top = Math.ceil(rawTop / step) * step;

  const average = summary?.average_per_session != null ? Number(summary.average_per_session) : null;
  const averageTop =
    average != null && average > 0 && top > 0
      ? Math.max(0, Math.min(PLOT_HEIGHT, PLOT_HEIGHT * (1 - average / top)))
      : null;

  const selIndex = selKey == null ? -1 : rows.findIndex((r) => r.key === selKey);
  const selected = selIndex >= 0 ? selIndex : highestIndex(rows);
  const selectedRow = rows[selected];

  const pitch = labelPitch(plotWidth, rows.length, gap);
  const showLabels = useMemo(() => thinnedLabels(rows.length, pitch), [rows.length, pitch]);

  const move = (delta: number) => {
    if (rows.length === 0) return;
    const next = (selected + delta + rows.length) % rows.length;
    setSelKey(rows[next].key);
    btnRefs.current[next]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Home') {
      e.preventDefault();
      setSelKey(rows[0]?.key ?? null);
      btnRefs.current[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      const last = rows.length - 1;
      setSelKey(rows[last]?.key ?? null);
      btnRefs.current[last]?.focus();
    }
  };

  const sentence = sessionSentence(unit, rows, summary);
  const unitPlural = `${unit}s`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
      {sentence && (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-base)', lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
          {sentence}
        </p>
      )}
      <div
        role="group"
        aria-label={`Model cost per session, ${spanLabel(rows)}, stacked by purpose. Use the arrow keys to step through the ${unitPlural}.`}
        onKeyDown={onKeyDown}
        style={{ display: 'flex', gap: 10, alignItems: 'flex-start', minWidth: 0 }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            height: PLOT_HEIGHT,
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-xs)',
            color: 'var(--sa-text-muted)',
            textAlign: 'right',
            paddingRight: 4,
            flex: '0 0 auto',
          }}
        >
          <span>{fmtUsd(String(top))}</span>
          <span>{fmtUsd(String(top / 2))}</span>
          <span>$0</span>
        </div>
        <div style={{ position: 'relative', flex: '1 1 auto', minWidth: 0 }}>
          <div aria-hidden="true" style={{ position: 'absolute', left: 0, right: 0, top: 0, height: PLOT_HEIGHT, pointerEvents: 'none' }}>
            <div style={{ position: 'absolute', left: 0, right: 0, top: 0, borderTop: '1px dashed var(--sa-border)' }} />
            <div style={{ position: 'absolute', left: 0, right: 0, top: PLOT_HEIGHT / 2, borderTop: '1px dashed var(--sa-border)' }} />
            <div style={{ position: 'absolute', left: 0, right: 0, top: PLOT_HEIGHT, borderTop: '1px solid var(--sa-border)' }} />
            {averageTop != null && (
              <div
                title={`Average ${fmtUsd(summary?.average_per_session)} a session`}
                style={{ position: 'absolute', left: 0, right: 0, top: averageTop, borderTop: '1px dotted var(--sa-text-2)', opacity: 0.6 }}
              />
            )}
          </div>
          <div ref={barsRef} style={{ position: 'relative', display: 'flex', gap, alignItems: 'flex-start' }}>
            {rows.map((r, i) => {
              const total = totals[i];
              const parts = STACK.map((s) => ({
                ...s,
                cost: Number(r[s.key]),
              })).filter((p) => p.cost > 0);
              const isSel = i === selected;
              return (
                <button
                  key={r.key}
                  ref={(el) => {
                    btnRefs.current[i] = el;
                  }}
                  type="button"
                  aria-pressed={isSel}
                  aria-label={`${bucketDates(r, unit)}, ${fmtUsd(total.toFixed(4))}`}
                  title={`${bucketDates(r, unit)} · ${fmtUsd(total.toFixed(4))} · ${parts.map((p) => `${p.label.toLowerCase()} ${fmtUsd(p.cost.toFixed(4))}`).join(' · ')}`}
                  tabIndex={isSel ? 0 : -1}
                  onClick={() => setSelKey(r.key)}
                  style={{
                    flex: '1 1 0',
                    minWidth: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 6,
                    padding: 0,
                    border: 0,
                    background: 'transparent',
                    cursor: 'pointer',
                    font: 'inherit',
                  }}
                >
                  <div style={{ height: PLOT_HEIGHT, width: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center' }}>
                    <div
                      style={{
                        width: `min(100%, ${BAR_MAX_WIDTH}px)`,
                        display: 'flex',
                        flexDirection: 'column',
                        borderRadius: '3px 3px 0 0',
                        overflow: 'hidden',
                        boxShadow: isSel ? '0 0 0 2px var(--sa-text)' : undefined,
                      }}
                    >
                      {[...parts].reverse().map((p) => (
                        <div
                          key={p.key}
                          style={{ height: Math.max(1, (p.cost / top) * PLOT_HEIGHT), background: p.color }}
                        />
                      ))}
                    </div>
                  </div>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-xs)',
                      color: isSel ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {showLabels[i] ? r.label : ''}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      {selectedRow && (
        <div
          aria-live="polite"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'baseline',
            gap: '4px 14px',
            padding: '10px 12px',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-raised)',
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
          }}
        >
          <span style={{ fontWeight: 600, color: 'var(--sa-text)' }}>
            {bucketDates(selectedRow, unit)} ·{' '}
            <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{fmtUsd(totals[selected].toFixed(4))}</span>
          </span>
          {STACK.map((s) => (
            <span key={s.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
              <span style={{ width: 10, height: 10, background: s.color, borderRadius: 2 }} />
              {s.detail}{' '}
              <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{fmtUsd(selectedRow[s.key])}</span>
            </span>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 16px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {STACK.map((s) => (
          <span key={s.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 10, height: 10, background: s.color, borderRadius: 2 }} />
            {s.label}
          </span>
        ))}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, borderTop: '1px dotted var(--sa-text-2)' }} />
          average
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.55 }}>
        One bar per session up to 31 sessions, per week up to 31 weeks, per month up to 31 months, per quarter beyond that, so the bars always fit. Click or tap a bar for its amounts. Backtests run per run, not per session; they are in By purpose above.
      </p>
    </div>
  );
}
