import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import {
  deleteBacktest,
  getBacktestDecisions,
  getBacktestTrades,
  getBacktests,
  postBacktest,
  postBacktestCancel,
  postBacktestEstimate,
  postClearBarStore,
} from '../api/state';
import { Banner } from '../components/Banner';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import type { ChipVariant } from '../components/Chip';
import { StrategyEquityChart } from '../components/StrategyEquityChart';
import type { StrategySeries } from '../components/StrategyEquityChart';
import type {
  BacktestDecisionRow,
  BacktestEstimate,
  BacktestRun,
  BacktestStrategyRef,
  BacktestStrategySummary,
  BacktestTrade,
  BacktestsResponse,
} from '../types/api';
import { displayDateTime } from '../format';

// Strategy colours in picker order (design: Backtest).
const PALETTE = ['#F2B94B', '#9FB4CC', '#7FB8FF', '#C792EA'];

const STATUS_CHIP: Record<string, ChipVariant> = {
  queued: 'info',
  downloading: 'info',
  running: 'info',
  done: 'positive',
  cancelled: 'warning',
  interrupted: 'warning',
  refused: 'negative',
  failed: 'negative',
};

const ACTIVE = new Set(['queued', 'downloading', 'running']);

const inputStyle: CSSProperties = {
  height: 'var(--sa-input-height)',
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-bg)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-md)',
  width: '100%',
  padding: '0 10px',
  fontFamily: 'var(--sa-font-mono)',
};

const labelStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--sa-space-2)',
  minWidth: 0,
};

const labelText: CSSProperties = {
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
};

const smallButton: CSSProperties = {
  height: '32px',
  padding: '0 10px',
  borderRadius: '6px',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-raised)',
  color: 'var(--sa-text-2)',
  fontSize: 'var(--sa-text-sm)',
  cursor: 'pointer',
};

const actionButton: CSSProperties = {
  height: '40px',
  padding: '0 16px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '8px',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-raised)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-md)',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  boxSizing: 'border-box',
};

const ghostButton: CSSProperties = {
  ...actionButton,
  border: '1px solid var(--sa-border)',
  background: 'transparent',
  color: 'var(--sa-text-2)',
};

const th: CSSProperties = {
  textAlign: 'right',
  padding: '8px 10px',
  fontSize: 'var(--sa-text-sm)',
  fontWeight: 500,
  color: 'var(--sa-text-muted)',
  borderBottom: '1px solid var(--sa-border)',
  whiteSpace: 'nowrap',
};
const td: CSSProperties = {
  padding: '10px',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
  verticalAlign: 'top',
  textAlign: 'right',
  fontFamily: 'var(--sa-font-mono)',
};

interface PickOption {
  ref: string;
  name: string;
  sub: string;
  model?: string | null;
}

function pickOptions(data: BacktestsResponse): PickOption[] {
  const out: PickOption[] = [
    {
      ref: 'live',
      name: `Live settings`,
      sub: `${data.available.live.prompt_version ?? '—'} · ${data.available.live.model ?? '—'}`,
      model: data.available.live.model,
    },
    { ref: 'rules_only', name: 'Rules only', sub: 'no model · free' },
  ];
  for (const s of data.available.shadows) {
    out.push({
      ref: s.ref,
      name: s.name,
      sub:
        s.kind === 'rules_only'
          ? 'no model · free'
          : `${s.prompt_version ?? '—'} · ${s.model ?? '—'}`,
      model: s.model,
    });
  }
  return out;
}

function cutoffWarning(
  model: string | null | undefined,
  cutoffs: Record<string, string>,
  start: string,
): string | null {
  const cutoff = model ? cutoffs[model] : undefined;
  if (!cutoff || !start) return null;
  if (start > cutoff) return null;
  const month = new Date(`${cutoff}T12:00:00Z`).toLocaleDateString('en-GB', {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
  return `Knowledge cutoff ${month}: needs a later start`;
}

function cutoffOkLine(estimate: BacktestEstimate): string | null {
  if (!estimate.cutoff || !estimate.cutoff_label) return null;
  const month = new Date(`${estimate.cutoff}T12:00:00Z`).toLocaleDateString(
    'en-GB',
    { month: 'short', year: 'numeric', timeZone: 'UTC' },
  );
  return `Period starts after ${estimate.cutoff_label}'s knowledge cutoff (${month})`;
}

function compactK(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

function fmtMB(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  return `${Math.round(bytes / 1_000_000)} MB`;
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}%`;
}

function fmtR(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}R`;
}

function fmtPts(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}`;
}

function progressFraction(progress: string | null): number | null {
  if (!progress) return null;
  const m = /(\d+)\s+of\s+(\d+)/.exec(progress);
  if (!m) return null;
  const total = Number(m[2]);
  return total > 0 ? Math.min(1, Number(m[1]) / total) : null;
}

function strategyColour(run: BacktestRun, slug: string): string {
  const index = run.strategies.findIndex((s) => s.slug === slug);
  return PALETTE[index >= 0 ? index % PALETTE.length : 0];
}

function summaryOf(
  run: BacktestRun,
): [BacktestStrategyRef, BacktestStrategySummary][] {
  if (!run.summary) return [];
  return run.strategies
    .map((s) => [s, run.summary?.[s.slug]] as const)
    .filter((pair): pair is [BacktestStrategyRef, BacktestStrategySummary] => pair[1] != null);
}

export default function Backtest() {
  const location = useLocation();
  const [data, setData] = useState<BacktestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [panel, setPanel] = useState<'trades' | 'decisions' | 'csv' | null>(null);

  const refresh = useCallback(() => {
    getBacktests()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(refresh, [refresh]);
  useLiveUpdates(['backtests'], refresh);

  // The manager ticks in the background; poll while a run is active so the
  // progress bar moves without a websocket publish.
  const hasActive = data?.runs.some((r) => ACTIVE.has(r.status)) ?? false;
  useEffect(() => {
    if (!hasActive) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [hasActive, refresh]);

  const selected =
    data?.runs.find((r) => r.id === selectedId) ??
    data?.runs.find((r) => r.status === 'done' || r.status === 'refused') ??
    data?.runs[0] ??
    null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)' }}>
      {error && <Banner variant="negative">Action failed: {error}</Banner>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 320px', maxWidth: 400, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)', minWidth: 0 }}>
          <NewBacktestCard
            data={data}
            onCreated={(id, refused) => {
              refresh();
              if (refused) setSelectedId(id);
            }}
            onError={setError}
          />
          <RunsCard
            data={data}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
            onCancel={(id) => {
              postBacktestCancel(id).then(refresh).catch((e) => setError(errorMessages(e)[0]));
            }}
            onClear={() => {
              postClearBarStore().then(refresh).catch((e) => setError(errorMessages(e)[0]));
            }}
            onDelete={(id) => {
              deleteBacktest(id).then(refresh).catch((e) => setError(errorMessages(e)[0]));
            }}
          />
        </div>
        <div style={{ flex: '999 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)' }}>
          {selected ? (
            <RunDetail
              run={selected}
              panel={panel}
              onPanel={setPanel}
              location={location}
            />
          ) : (
            <Card label="Results">
              <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
                No backtests yet. Pick a period and strategies on the left, then
                Run backtest.
              </span>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function NewBacktestCard({
  data,
  onCreated,
  onError,
}: {
  data: BacktestsResponse | null;
  onCreated: (id: number, refused: boolean) => void;
  onError: (msg: string | null) => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState(today);
  const [universe, setUniverse] = useState('fixed');
  const [picked, setPicked] = useState<string[]>(['live', 'rules_only']);
  const [equity, setEquity] = useState('10000');
  const [slippage, setSlippage] = useState('0.05');
  const [estimate, setEstimate] = useState<BacktestEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [running, setRunning] = useState(false);
  const seq = useRef(0);

  const options = useMemo(() => (data ? pickOptions(data) : []), [data]);
  const cutoffs = data?.available.model_cutoffs ?? {};

  const body = useMemo(
    () => ({
      start,
      end,
      universe_mode: universe,
      strategies: picked,
      starting_equity: Number(equity) || 0,
      slippage_pct: Number(slippage) || 0,
    }),
    [start, end, universe, picked, equity, slippage],
  );

  useEffect(() => {
    if (!start || !end || picked.length === 0) {
      setEstimate(null);
      return;
    }
    const ticket = ++seq.current;
    setEstimating(true);
    const t = setTimeout(() => {
      postBacktestEstimate(body)
        .then((est) => {
          if (ticket === seq.current) setEstimate(est);
        })
        .catch(() => {
          if (ticket === seq.current) setEstimate(null);
        })
        .finally(() => {
          if (ticket === seq.current) setEstimating(false);
        });
    }, 350);
    return () => clearTimeout(t);
  }, [body, start, end, picked.length]);

  const quick = (from: string) => {
    setStart(from);
    setEnd(today);
  };
  const monthsAgo = (months: number) => {
    const d = new Date();
    d.setMonth(d.getMonth() - months);
    return d.toISOString().slice(0, 10);
  };
  const latestCutoff = picked
    .map((ref) => options.find((o) => o.ref === ref)?.model)
    .map((m) => (m ? cutoffs[m] : undefined))
    .filter((c): c is string => !!c)
    .sort()
    .pop();
  const sinceCutoff = latestCutoff
    ? new Date(new Date(`${latestCutoff}T00:00:00Z`).getTime() + 86400000)
        .toISOString()
        .slice(0, 10)
    : today;

  const disabled =
    running ||
    estimating ||
    !start ||
    !end ||
    picked.length === 0 ||
    estimate?.disabled_reason != null;

  const run = () => {
    setRunning(true);
    postBacktest(body)
      .then((r) => onCreated(r.id, r.status === 'refused'))
      .catch((e) => onError(errorMessages(e)[0]))
      .finally(() => setRunning(false));
  };

  const toggle = (ref: string) =>
    setPicked((cur) =>
      cur.includes(ref) ? cur.filter((r) => r !== ref) : [...cur, ref],
    );

  return (
    <Card label="New backtest">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
          <label style={labelStyle}>
            <span style={labelText}>From</span>
            <input
              type="date"
              style={inputStyle}
              value={start}
              max={end}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            <span style={labelText}>To</span>
            <input
              type="date"
              style={inputStyle}
              value={end}
              min={start}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          <button type="button" style={smallButton} onClick={() => quick(monthsAgo(3))}>
            Last 3 months
          </button>
          <button type="button" style={smallButton} onClick={() => quick(monthsAgo(12))}>
            Last 12 months
          </button>
          <button type="button" style={smallButton} onClick={() => quick('2024-01-02')}>
            Since 2024
          </button>
          <button
            type="button"
            style={smallButton}
            title={
              latestCutoff
                ? `Starts the day after the latest picked model's cutoff`
                : 'No LLM strategy picked'
            }
            onClick={() => quick(sinceCutoff)}
          >
            Since model cutoff
          </button>
        </div>
        <label style={labelStyle}>
          <span style={labelText}>Universe</span>
          <select style={inputStyle} value={universe} onChange={(e) => setUniverse(e.target.value)}>
            <option value="fixed">
              Fixed list · {data?.available.universe.fixed_count ?? '—'} symbols
            </option>
            <option value="screened">Screened pool · as screened each day</option>
          </select>
        </label>
        <div>
          <div style={{ ...labelText, marginBottom: '2px' }}>Strategies to compare</div>
          {options.map((o, i) => {
            const checked = picked.includes(o.ref);
            const warn = cutoffWarning(o.model, cutoffs, start);
            return (
              <div key={o.ref} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', padding: '8px 0' }}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={checked}
                  aria-label={o.name}
                  onClick={() => toggle(o.ref)}
                  style={{
                    marginTop: '1px',
                    width: '22px',
                    height: '22px',
                    flex: '0 0 22px',
                    borderRadius: '5px',
                    border: `1px solid ${checked ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
                    background: checked ? 'var(--sa-accent)' : 'var(--sa-bg)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: 0,
                    cursor: 'pointer',
                  }}
                >
                  {checked && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0B0E13" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="5 12 10 17 19 7" />
                    </svg>
                  )}
                </button>
                <span
                  style={{
                    width: '10px',
                    height: '10px',
                    marginTop: '6px',
                    borderRadius: '3px',
                    background: PALETTE[i % PALETTE.length],
                    flex: '0 0 10px',
                  }}
                />
                <span style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                  <span style={{ fontSize: 'var(--sa-text-md)' }}>{o.name}</span>
                  <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{o.sub}</span>
                  {warn && (
                    <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{warn}</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
          <label style={labelStyle}>
            <span style={labelText}>Starting equity</span>
            <input
              style={inputStyle}
              value={equity}
              inputMode="decimal"
              onChange={(e) => setEquity(e.target.value.replace(/[^0-9.]/g, ''))}
            />
          </label>
          <label style={labelStyle}>
            <span style={labelText}>Slippage per side</span>
            <input
              style={inputStyle}
              value={slippage}
              inputMode="decimal"
              onChange={(e) => setSlippage(e.target.value.replace(/[^0-9.]/g, ''))}
            />
          </label>
        </div>
        {estimate && (
          <div
            style={{
              padding: '12px 14px',
              border: '1px solid var(--sa-border)',
              background: 'var(--sa-raised)',
              borderRadius: 'var(--sa-radius-md)',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '10px 12px',
              fontSize: 'var(--sa-text-md)',
            }}
          >
            <EstimateCell label="Sessions" value={String(estimate.sessions)} />
            <EstimateCell
              label="Bars to download"
              value={`${compactK(estimate.bars_to_download)} · ≈ ${estimate.download_minutes} min`}
            />
            <EstimateCell
              label="Model calls (estimate)"
              value={`${estimate.calls.toLocaleString()}`}
              sub={`· ${estimate.cache_hits.toLocaleString()} cached (estimate)`}
            />
            <EstimateCell
              label="Model cost (estimate)"
              value={`≈ ${money(estimate.cost_usd)} of ${money(estimate.cap_remaining_usd)} left`}
              sub={`up to ${money(estimate.cap_cost_usd)} at the 10-call cap`}
            />
            {estimate.disabled_reason ? (
              <EstimateNote variant="negative">{estimate.disabled_reason}</EstimateNote>
            ) : cutoffOkLine(estimate) ? (
              <EstimateNote variant="positive">{cutoffOkLine(estimate)}</EstimateNote>
            ) : null}
            {estimate.may_stop_early && !estimate.disabled_reason && (
              <EstimateNote variant="warning">
                May stop early at this month's ${'$'}20 cap.
              </EstimateNote>
            )}
          </div>
        )}
        <button
          type="button"
          disabled={disabled}
          onClick={run}
          style={{
            height: '44px',
            padding: '0 16px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-accent)',
            background: 'var(--sa-accent)',
            color: '#0B0E13',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: disabled ? 'default' : 'pointer',
            opacity: disabled ? 0.5 : 1,
            whiteSpace: 'nowrap',
            boxSizing: 'border-box',
          }}
        >
          {running ? 'Queuing…' : 'Run backtest'}
        </button>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
          Runs as a separate background process, one at a time, with one
          database per strategy. It never touches the live account, uses at
          most 40 Alpaca calls per minute, and pauses downloads and model
          calls while a live cycle or check runs.
        </span>
      </div>
    </Card>
  );
}

function EstimateCell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{label}</div>
      <div style={{ fontFamily: 'var(--sa-font-mono)' }}>
        {value}
        {sub && <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}> {sub}</span>}
      </div>
    </div>
  );
}

function EstimateNote({ variant, children }: { variant: 'positive' | 'negative' | 'warning'; children: ReactNode }) {
  const colour =
    variant === 'positive'
      ? 'var(--sa-positive)'
      : variant === 'negative'
        ? 'var(--sa-negative-text)'
        : 'var(--sa-warning-text)';
  return (
    <div style={{ gridColumn: '1 / -1', display: 'flex', gap: '6px', alignItems: 'flex-start', color: colour, fontSize: 'var(--sa-text-sm)' }}>
      {variant !== 'negative' ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
          <polyline points="5 12 10 17 19 7" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12" y2="16" />
        </svg>
      )}
      <span>{children}</span>
    </div>
  );
}

function RunsCard({
  data,
  selectedId,
  onSelect,
  onCancel,
  onClear,
  onDelete,
}: {
  data: BacktestsResponse | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCancel: (id: number) => void;
  onClear: () => void;
  onDelete: (id: number) => void;
}) {
  const store = data?.store;
  const cap = data?.monthly_cap ?? 20;
  const spent = store?.month_spent_usd ?? 0;
  const anyActive = data?.runs.some((r) => ACTIVE.has(r.status)) ?? false;
  return (
    <Card
      label="Runs"
      right={
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {money(spent)} of {money(cap)} this month
        </span>
      }
    >
      {data?.runs.map((r) => (
        <button
          key={r.id}
          type="button"
          onClick={() => onSelect(r.id)}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            padding: '10px 12px',
            borderRadius: '6px',
            border: 'none',
            textAlign: 'left',
            cursor: 'pointer',
            fontFamily: 'inherit',
            color: 'var(--sa-text)',
            background: r.id === selectedId ? 'var(--sa-accent-bg)' : 'transparent',
            boxShadow: r.id === selectedId ? 'inset 3px 0 0 var(--sa-accent)' : 'none',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--sa-text-md)' }}>
              #{r.id} {r.name}
            </span>
            <Chip variant={STATUS_CHIP[r.status] ?? 'neutral'}>{r.status}</Chip>
          </div>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {displayDateTime(r.created_at)} · {r.strategies.length}{' '}
            {r.strategies.length === 1 ? 'strategy' : 'strategies'}
            {r.cost_usd > 0 ? ` · ${money(r.cost_usd)}` : ''}
          </span>
          {ACTIVE.has(r.status) && (
            <>
              <div style={{ height: '6px', borderRadius: '3px', background: 'var(--sa-border)' }}>
                <div
                  style={{
                    width: `${Math.round((progressFraction(r.progress) ?? 0) * 100)}%`,
                    height: '6px',
                    borderRadius: '3px',
                    background: 'var(--sa-info)',
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                <span>{r.progress ?? 'preparing'}</span>
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onCancel(r.id);
                  }}
                >
                  Cancel
                </a>
              </div>
            </>
          )}
          {r.status === 'refused' && r.refuse_reason && (
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-negative-text)' }}>
              {r.refuse_reason}
            </span>
          )}
          {!ACTIVE.has(r.status) && (
            <a
              href="#"
              style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-disabled)', alignSelf: 'flex-end' }}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onDelete(r.id);
              }}
            >
              Delete
            </a>
          )}
        </button>
      ))}
      {(!data || data.runs.length === 0) && (
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          No runs yet.
        </span>
      )}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: '8px',
          padding: '10px 12px 2px',
          borderTop: '1px solid var(--sa-border)',
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        <span>
          Bar store {fmtMB(store?.size_bytes ?? 0)}
          {store?.earliest ? ` · ${store.earliest} – ${store.latest ?? 'today'}` : ''}
        </span>
        <a
          href="#"
          title="Only when no backtest is running"
          style={{ color: anyActive ? 'var(--sa-text-disabled)' : undefined, whiteSpace: 'nowrap' }}
          onClick={(e) => {
            e.preventDefault();
            if (!anyActive) onClear();
          }}
        >
          Clear bar store
        </a>
      </div>
    </Card>
  );
}

function RunDetail({
  run,
  panel,
  onPanel,
  location,
}: {
  run: BacktestRun;
  panel: 'trades' | 'decisions' | 'csv' | null;
  onPanel: (p: 'trades' | 'decisions' | 'csv' | null) => void;
  location: ReturnType<typeof useLocation>;
}) {
  const summaries = summaryOf(run);
  const totalTrades = summaries.reduce((n, [, s]) => n + s.closed_trades, 0);

  const series: StrategySeries[] = useMemo(
    () => [
      ...summaries.map(([ref, s]) => ({
        name: ref.name,
        colour: strategyColour(run, ref.slug),
        points: s.curve.map((p) => ({
          date: p.date,
          pct: (p.equity / Number(run.starting_equity) - 1) * 100,
        })),
      })),
      ...summaries.slice(0, 1).map(([, s]) => ({
        name: 'SPY buy and hold',
        colour: '#C9CFD6',
        points: s.spy_curve.map((p) => ({
          date: p.date,
          pct: (p.equity / Number(run.starting_equity) - 1) * 100,
        })),
        dashed: true,
      })),
    ],
    [summaries, run],
  );

  return (
    <>
      <section style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '6px 16px' }}>
        <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 600 }}>
          #{run.id} · {run.name}
        </h2>
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {run.start} – {run.end} · {run.universe_mode} list · $
          {Number(run.starting_equity).toLocaleString()}
          {run.finished_at ? ` · finished ${displayDateTime(run.finished_at)}` : ''}
          {run.cost_usd > 0 ? ` · ${money(run.cost_usd)}` : ''}
        </span>
      </section>
      {summaries.length > 0 && (
        <Card label="Results">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            <MetricCell label="Return" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), fmtPct(s.return_pct)])} />
            <MetricCell
              label="vs SPY buy and hold, pts"
              title={`SPY returned ${fmtPct(summaries[0]?.[1].spy_return_pct)} over the same sessions`}
              values={summaries.map(([r, s]) => [strategyColour(run, r.slug), fmtPts(s.vs_spy_pts)])}
            />
            <MetricCell label="Max drawdown" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), s.max_drawdown_pct == null ? '—' : `−${Math.abs(s.max_drawdown_pct).toFixed(1)}%`])} />
            <MetricCell label="Closed trades" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), String(s.closed_trades)])} />
            <MetricCell label="Win rate" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), s.win_rate == null ? '—' : `${Math.round(s.win_rate * 100)}%`])} />
            <MetricCell label="Average R" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), fmtR(s.avg_r)])} />
            <MetricCell label="Profit factor" values={summaries.map(([r, s]) => [strategyColour(run, r.slug), s.profit_factor == null ? '—' : s.profit_factor.toFixed(2)])} />
            <MetricCell
              label="Time in market"
              title="Share of sessions with at least one position open"
              values={summaries.map(([r, s]) => [strategyColour(run, r.slug), s.time_in_market == null ? '—' : `${Math.round(s.time_in_market * 100)}%`])}
            />
          </div>
          <div style={{ display: 'flex', gap: '16px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', flexWrap: 'wrap' }}>
            {summaries.map(([ref]) => (
              <span key={ref.slug} style={{ display: 'inline-flex', gap: '6px', alignItems: 'center' }}>
                <span style={{ width: '10px', height: '10px', borderRadius: '3px', background: strategyColour(run, ref.slug) }} />
                {ref.name}
              </span>
            ))}
            <span style={{ display: 'inline-flex', gap: '6px', alignItems: 'center' }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '3px', background: '#C9CFD6' }} />
              SPY buy and hold
            </span>
          </div>
        </Card>
      )}
      {summaries.length > 0 && (
        <Card label="Equity" right={<span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>% of starting equity</span>}>
          <StrategyEquityChart series={series} />
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            Dashed: SPY buy and hold over the same sessions.
          </div>
        </Card>
      )}
      {summaries.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)', alignItems: 'stretch' }}>
          <RegimeCard run={run} summaries={summaries} />
          <ReadWithCare run={run} summaries={summaries} />
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        <button
          type="button"
          style={panel === 'trades' ? actionButton : ghostButton}
          onClick={() => onPanel(panel === 'trades' ? null : 'trades')}
        >
          Open trades{summaries.length ? ` (${totalTrades})` : ''}
        </button>
        <button
          type="button"
          style={panel === 'decisions' ? actionButton : ghostButton}
          onClick={() => onPanel(panel === 'decisions' ? null : 'decisions')}
        >
          Open decisions
        </button>
        <button
          type="button"
          style={panel === 'csv' ? actionButton : ghostButton}
          onClick={() => onPanel(panel === 'csv' ? null : 'csv')}
        >
          Download CSV
        </button>
      </div>
      {panel === 'trades' && <TradesPanel run={run} />}
      {panel === 'decisions' && <DecisionsPanel run={run} location={location} />}
      {panel === 'csv' && <CsvPanel run={run} />}
    </>
  );
}

function MetricCell({
  label,
  values,
  title,
}: {
  label: string;
  values: [string, string][];
  title?: string;
}) {
  return (
    <div
      style={{
        flex: '1 1 150px',
        padding: '12px 14px',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        minWidth: 0,
      }}
    >
      <span title={title} style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', textDecoration: title ? 'underline dotted' : undefined, textUnderlineOffset: '3px', cursor: title ? 'help' : undefined, alignSelf: 'flex-start' }}>
        {label}
      </span>
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', fontFamily: 'var(--sa-font-mono)', fontSize: '15px' }}>
        {values.map(([colour, v], i) => (
          <span key={i} style={{ color: colour }}>{v}</span>
        ))}
      </span>
    </div>
  );
}

function RegimeCard({
  run,
  summaries,
}: {
  run: BacktestRun;
  summaries: [BacktestStrategyRef, BacktestStrategySummary][];
}) {
  const regimes = [...new Set(summaries.flatMap(([, s]) => Object.keys(s.by_regime)))].sort();
  return (
    <Card label="By market regime">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: '320px', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>Regime</th>
              {summaries.map(([ref]) => (
                <Fragment key={ref.slug}>
                  <th style={{ ...th, color: strategyColour(run, ref.slug) }}>{ref.name}</th>
                  <th style={th}>Avg R</th>
                </Fragment>
              ))}
            </tr>
          </thead>
          <tbody>
            {regimes.map((regime) => (
              <tr key={regime}>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>{regime.replace(/_/g, ' · ')}</td>
                {summaries.map(([ref, s]) => {
                  const row = s.by_regime[regime];
                  return (
                    <Fragment key={ref.slug}>
                      <td style={td}>{row?.trades ?? '—'}</td>
                      <td style={td}>{fmtR(row?.avg_r)}</td>
                    </Fragment>
                  );
                })}
              </tr>
            ))}
            {regimes.length === 0 && (
              <tr>
                <td colSpan={1 + summaries.length * 2} style={{ ...td, textAlign: 'left', fontFamily: 'inherit', color: 'var(--sa-text-muted)' }}>
                  No closed trades.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        Closed trades and average R per strategy. Down with high volatility blocks entries.
      </div>
    </Card>
  );
}

function ReadWithCare({
  run,
  summaries,
}: {
  run: BacktestRun;
  summaries: [BacktestStrategyRef, BacktestStrategySummary][];
}) {
  const warnings = run.warnings ?? [];
  const cached = summaries.reduce((n, [, s]) => n + s.cache_hits, 0);
  const calls = summaries.reduce((n, [, s]) => n + s.calls, 0);
  return (
    <Card label="Read with care">
      <ul style={{ margin: 0, paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '6px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
        <li>
          No historical quotes on the free data plan: spreads assumed at{' '}
          {Number(run.slippage_pct)}%, so the spread check never blocked an
          entry.
        </li>
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
        <li>
          {cached.toLocaleString()} of {calls.toLocaleString()} model answers
          came from the cache of earlier runs with identical inputs.
        </li>
      </ul>
    </Card>
  );
}

function TradesPanel({ run }: { run: BacktestRun }) {
  return (
    <>
      {run.strategies.map((ref) => (
        <StrategyTrades key={ref.slug} run={run} ref_={ref} />
      ))}
    </>
  );
}

function StrategyTrades({ run, ref_ }: { run: BacktestRun; ref_: BacktestStrategyRef }) {
  const [trades, setTrades] = useState<BacktestTrade[] | null>(null);
  useEffect(() => {
    getBacktestTrades(run.id, ref_.slug)
      .then((r) => setTrades(r.trades))
      .catch(console.error);
  }, [run.id, ref_.slug]);
  return (
    <Card label={`Trades · ${ref_.name}`}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: '520px', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>Symbol</th>
              <th style={th}>Qty</th>
              <th style={th}>Entry</th>
              <th style={th}>Opened</th>
              <th style={th}>Closed</th>
              <th style={th}>Exit</th>
              <th style={th}>P&L</th>
            </tr>
          </thead>
          <tbody>
            {(trades ?? []).map((t) => (
              <tr key={t.id}>
                <td style={{ ...td, textAlign: 'left' }}>{t.symbol}</td>
                <td style={td}>{t.qty}</td>
                <td style={td}>{t.entry}</td>
                <td style={td}>{displayDateTime(t.opened_at)}</td>
                <td style={td}>{displayDateTime(t.closed_at)}</td>
                <td style={{ ...td, fontFamily: 'inherit' }}>{t.exit_reason ?? '—'}</td>
                <td
                  style={{
                    ...td,
                    color: Number(t.pnl_adjusted ?? 0) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)',
                  }}
                >
                  {t.pnl_adjusted ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {trades !== null && trades.length === 0 && (
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>No trades.</span>
      )}
    </Card>
  );
}

function DecisionsPanel({
  run,
  location,
}: {
  run: BacktestRun;
  location: ReturnType<typeof useLocation>;
}) {
  return (
    <>
      {run.strategies.map((ref) => (
        <StrategyDecisions key={ref.slug} run={run} ref_={ref} location={location} />
      ))}
    </>
  );
}

function StrategyDecisions({
  run,
  ref_,
  location,
}: {
  run: BacktestRun;
  ref_: BacktestStrategyRef;
  location: ReturnType<typeof useLocation>;
}) {
  const [rows, setRows] = useState<BacktestDecisionRow[] | null>(null);
  useEffect(() => {
    getBacktestDecisions(run.id, ref_.slug)
      .then((r) => setRows(r.items))
      .catch(console.error);
  }, [run.id, ref_.slug]);
  return (
    <Card label={`Decisions · ${ref_.name}`}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: '480px', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>Cycle</th>
              <th style={{ ...th, textAlign: 'left' }}>Symbol</th>
              <th style={{ ...th, textAlign: 'left' }}>Context</th>
              <th style={{ ...th, textAlign: 'left' }}>Action</th>
              <th style={{ ...th, textAlign: 'left' }}>Source</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((d) => (
              <tr key={d.id}>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>
                  <Link
                    to={`/decisions/${d.id}?bt=${run.id}&s=${ref_.slug}`}
                    state={{ backgroundLocation: location }}
                  >
                    {displayDateTime(d.cycle_time)}
                  </Link>
                </td>
                <td style={{ ...td, textAlign: 'left' }}>{d.symbol}</td>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>{d.context}</td>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>
                  {d.no_call_reason ? `no_call · ${d.no_call_reason}` : d.final_action}
                </td>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>
                  {d.cached ? 'cache' : 'model'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows !== null && rows.length === 0 && (
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>No decisions.</span>
      )}
    </Card>
  );
}

function CsvPanel({ run }: { run: BacktestRun }) {
  return (
    <Card label="Download CSV">
      <ul style={{ margin: 0, paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '6px', fontSize: 'var(--sa-text-md)' }}>
        {run.strategies.map((ref) => (
          <li key={ref.slug}>
            {ref.name}:{' '}
            <a href={`/api/backtests/${run.id}/strategies/${ref.slug}/export.csv?kind=trades`}>
              trades.csv
            </a>
            {' · '}
            <a href={`/api/backtests/${run.id}/strategies/${ref.slug}/export.csv?kind=decisions`}>
              decisions.csv
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}


