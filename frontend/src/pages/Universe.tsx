import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import {
  getUniverse,
  postUniverseAdd,
  postUniverseImport,
  postUniverseRemove,
} from '../api/state';
import type {
  UniverseFunnelStep,
  UniverseResponse,
  UniverseRow,
} from '../types/api';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';

// ---------------------------------------------------------------------------
// formatting

function usd(value: string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return `$${n.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function dollarVolumeM(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value) / 1_000_000;
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function vsSpy(value: string | null | undefined): { text: string; up: boolean | null } {
  if (value === null || value === undefined || value === '') return { text: '—', up: null };
  const n = Number(value);
  if (Number.isNaN(n)) return { text: '—', up: null };
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return { text: `${sign}${Math.abs(n).toFixed(1)}%`, up: n > 0 ? true : n < 0 ? false : null };
}

/** Wall-clock "08:10" in New York, rendered in Amsterdam time — computed per
 * day so DST is always right (never a hard-coded offset). */
function amsterdamForNy(hhmm: string): string {
  const [hh, mm] = hhmm.split(':').map(Number);
  if (Number.isNaN(hh) || Number.isNaN(mm)) return '';
  const nyDate = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  let guess = new Date(`${nyDate}T${hhmm}:00Z`);
  for (let i = 0; i < 3; i += 1) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: 'numeric',
      hour12: false,
    }).formatToParts(guess);
    const gh = Number(parts.find((p) => p.type === 'hour')?.value ?? 0) % 24;
    const gm = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
    const diff = hh * 60 + mm - (gh * 60 + gm);
    if (diff === 0) break;
    guess = new Date(guess.getTime() + diff * 60_000);
  }
  return guess.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

const CHECK_LABEL: Record<string, string> = {
  history: 'history loading',
  tradable: 'not active or tradable',
  min_price: 'price below the minimum',
  one_share: 'price too high for 1 share',
  dollar_volume: 'dollar volume too low',
};

const FUNNEL_LABEL: Record<string, string> = {
  pool: 'In the pool',
  tradable: 'Active and tradable at Alpaca',
};

function funnelLabel(step: UniverseFunnelStep, data: UniverseResponse): string {
  if (step.key in FUNNEL_LABEL) return FUNNEL_LABEL[step.key];
  if (step.key === 'history') {
    return `History loaded (${data.history.daily_sessions} daily bars, ${data.history.sessions_5m} sessions of 5-min bars)`;
  }
  if (step.key === 'min_price') return `Price ≥ ${usd(String(data.thresholds.min_price))}`;
  if (step.key === 'one_share') {
    const cap = data.screen?.max_position_usd;
    return `One share fits in max position${cap ? ` (${usd(cap)})` : ''}`;
  }
  if (step.key === 'dollar_volume') {
    return `${data.thresholds.lookback_sessions}-session average dollar volume ≥ ${usd(String(data.thresholds.min_avg_dollar_volume / 1_000_000))}M`;
  }
  return step.key;
}

const TABS = ['All', 'Eligible', 'Held', 'Excluded', 'Warming up'] as const;
type Tab = (typeof TABS)[number];

function inTab(row: UniverseRow, tab: Tab): boolean {
  switch (tab) {
    case 'Eligible':
      return row.status === 'eligible';
    case 'Held':
      return row.held;
    case 'Excluded':
      return row.status === 'excluded';
    case 'Warming up':
      return row.status === 'warming_up';
    default:
      return true;
  }
}

function StatusChip({ row }: { row: UniverseRow }) {
  if (row.status === 'held' && !row.screened) return <Chip variant="info">held</Chip>;
  if (row.held) return <Chip variant="info">held · {row.status}</Chip>;
  if (row.status === 'eligible') {
    return (
      <Chip variant="positive">
        {row.fails > 0 ? `eligible · ${row.fails} fail${row.fails === 1 ? '' : 's'}` : 'eligible'}
      </Chip>
    );
  }
  if (row.status === 'warming_up') return <Chip variant="warning">warming up</Chip>;
  if (row.status === 'excluded') return <Chip variant="negative">excluded</Chip>;
  return <Chip variant="neutral">{row.status}</Chip>;
}

// ---------------------------------------------------------------------------

export default function Universe() {
  const [data, setData] = useState<UniverseResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('All');
  const [find, setFind] = useState('');
  const [showAllRows, setShowAllRows] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [newSector, setNewSector] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [csv, setCsv] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getUniverse()
      .then((d) => {
        setData(d);
        setErrors([]);
      })
      .catch((e) => setErrors(errorMessages(e)));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);
  useLiveUpdates(['universe'], refresh);

  const rows = useMemo(() => {
    const all = (data?.rows ?? []).filter((r) => inTab(r, tab));
    const q = find.trim().toUpperCase();
    return q ? all.filter((r) => r.symbol.includes(q)) : all;
  }, [data, tab, find]);

  // The "All" pool view collapses its middle (design/screens/Universe.dc.html):
  // first rows, a "n more eligible symbols" expander, then the tail with the
  // warming-up and excluded rows. Filtering or searching shows everything.
  const filtered = tab !== 'All' || find.trim() !== '';
  const HEAD = 8;
  const TAIL = 4;
  const collapsible = !filtered && !showAllRows && rows.length > HEAD + TAIL + 2;
  const headRows = collapsible ? rows.slice(0, HEAD) : rows;
  const hiddenRows = collapsible ? rows.slice(HEAD, -TAIL) : [];
  const tailRows = collapsible ? rows.slice(-TAIL) : [];
  const renderRow = (r: UniverseRow) => {
    const vs = vsSpy(r.return_vs_spy);
    return (
      <tr key={r.symbol}>
        <td style={tdMono('var(--sa-text-muted)')}>{r.rank ?? '—'}</td>
        <td style={{ ...td(), fontWeight: 600 }}>{r.symbol}</td>
        <td style={{ ...td(), color: 'var(--sa-text-2)' }}>{r.sector || '—'}</td>
        <td style={tdMono()}>{usd(r.last_close, 2)}</td>
        <td style={tdMono()}>{dollarVolumeM(r.avg_dollar_volume)}</td>
        <td
          style={{
            ...tdMono(),
            color:
              vs.up === null
                ? 'var(--sa-text-muted)'
                : vs.up
                  ? 'var(--sa-positive)'
                  : 'var(--sa-negative)',
          }}
        >
          {vs.text}
        </td>
        <td style={td()}>
          <StatusChip row={r} />
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 4 }}>
            {r.detail}
            {r.held && r.qty > 0 ? `${r.detail ? ' · ' : ''}${r.qty} sh` : ''}
          </div>
        </td>
        <td style={{ ...td(), textAlign: 'right' }}>
          {r.in_pool && r.symbol !== 'SPY' && (
            <button
              type="button"
              aria-label={`Remove ${r.symbol} from the pool`}
              title="Remove from the pool"
              disabled={busy}
              onClick={() => removeSymbol(r.symbol)}
              style={{
                width: 44,
                height: 44,
                border: 0,
                borderRadius: 'var(--sa-radius-sm)',
                background: 'transparent',
                color: 'var(--sa-text-muted)',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="6" y1="6" x2="18" y2="18" />
                <line x1="18" y1="6" x2="6" y2="18" />
              </svg>
            </button>
          )}
        </td>
      </tr>
    );
  };

  if (!data) {
    return (
      <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto' }}>
        {errors.length > 0 && <Banner variant="negative">{errors.join(' ')}</Banner>}
      </div>
    );
  }

  const screened = data.mode === 'screened';
  const counts = data.counts;
  const heldSet = new Set(data.held_symbols);
  const ams = amsterdamForNy(data.screen_time_ny);
  const screen = data.screen;

  const excludedRows = (data.rows ?? []).filter((r) => r.status === 'excluded');
  const excludedReason =
    excludedRows
      .map((r) => r.failed_check)
      .find((k) => k && k in CHECK_LABEL) ?? null;
  const warmingRows = (data.rows ?? []).filter((r) => r.status === 'warming_up');

  const run = (work: () => Promise<unknown>, ok: (result: unknown) => void) => {
    setBusy(true);
    setErrors([]);
    work()
      .then(ok)
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setBusy(false));
  };

  const addSymbol = () => {
    if (!newSymbol.trim() || !newSector) return;
    run(
      () => postUniverseAdd(newSymbol.trim().toUpperCase(), newSector),
      () => {
        setNotice(`${newSymbol.trim().toUpperCase()} added — warming up.`);
        setNewSymbol('');
        setNewSector('');
        refresh();
      },
    );
  };

  const importCsv = () => {
    if (!csv.trim()) return;
    run(
      () => postUniverseImport(csv),
      (result) => {
        const r = result as { added: number; skipped: number };
        setNotice(
          `Imported ${r.added} symbol${r.added === 1 ? '' : 's'}` +
            (r.skipped ? ` · ${r.skipped} already in the pool` : '') +
            ' — warming up.',
        );
        setCsv('');
        setImportOpen(false);
        refresh();
      },
    );
  };

  const removeSymbol = (symbol: string) => {
    run(
      () => postUniverseRemove(symbol),
      () => {
        setNotice(`${symbol} removed from the pool.`);
        refresh();
      },
    );
  };

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
        {screened && data.pool && counts
          ? `Pool of ${data.pool.size} · ${counts.eligible} eligible for the next session`
          : `Fixed list · ${data.fixed_symbols.length} symbols`}
      </div>

      {errors.length > 0 && <Banner variant="negative">{errors.join(' ')}</Banner>}
      {notice && <Banner variant="info">{notice}</Banner>}
      {screen?.failed && (
        <Banner variant="warning">
          The screen failed — the previous eligible list is kept and shown below.
        </Banner>
      )}

      {/* mode banner */}
      <section
        aria-label="Universe mode"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '10px 16px',
          padding: 'var(--sa-space-3) var(--sa-space-4)',
          border: '1px solid var(--sa-info-border)',
          background: 'var(--sa-info-bg)',
          borderRadius: 'var(--sa-radius-lg)',
        }}
      >
        <Chip variant="info">{screened ? 'screened pool' : 'fixed list'}</Chip>
        <span style={{ flex: '1 1 420px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
          {screened
            ? data.preview
              ? `Screened at ${data.screen_time_ny} New York (${ams} Amsterdam) on trading days. The first screened screen switches cycles to the eligible list; until then cycles keep the fixed list.`
              : `Screened at ${data.screen_time_ny} New York (${ams} Amsterdam) on trading days. The eligible list is fixed for the whole session; held symbols are always managed, even if excluded or removed.`
            : 'The universe is the fixed list below. The daily screen below is a preview — the screened pool is switched on in Settings → Trading.'}
        </span>
        <Link
          to="/settings"
          style={{
            height: 36,
            padding: '0 16px',
            display: 'inline-flex',
            alignItems: 'center',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border)',
            background: 'transparent',
            color: 'var(--sa-text-2)',
            fontSize: 'var(--sa-text-base)',
            fontWeight: 600,
            textDecoration: 'none',
            whiteSpace: 'nowrap',
            boxSizing: 'border-box',
          }}
        >
          Screen settings
        </Link>
      </section>

      {!screened && (
        <Card
          label="Fixed list"
          right={`${data.fixed_symbols.length} symbols`}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {data.fixed_symbols.map((s) => (
              <span
                key={s.symbol}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  height: 36,
                  padding: '0 12px',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-raised)',
                  fontSize: 'var(--sa-text-md)',
                }}
              >
                <b style={{ fontWeight: 600 }}>{s.symbol}</b>
                <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                  {s.sector || 'no sector'}
                </span>
                {heldSet.has(s.symbol) && <Chip variant="info">held</Chip>}
              </span>
            ))}
          </div>
          <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
            The fixed list is edited in{' '}
            <Link to="/settings" style={{ color: 'var(--sa-accent)' }}>
              Settings → Trading
            </Link>
            . The screened pool is switched on in Settings → Trading — until then the daily screen runs as a preview below.
          </p>
        </Card>
      )}

      {data.pool && counts && (
        <>
          {/* summary tiles */}
          <section aria-label="Universe summary" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}>
            {(
              [
                ['Pool', String(data.pool.size), `limit ${data.pool.max}`, 'var(--sa-text)'],
                ['Eligible', String(counts.eligible), data.preview ? 'preview' : 'next session', 'var(--sa-accent)'],
                ['Held', String(counts.held), data.held_symbols.join(', ') || '—', 'var(--sa-text)'],
                [
                  'Excluded',
                  String(counts.excluded),
                  excludedReason ? CHECK_LABEL[excludedReason] : '—',
                  'var(--sa-negative)',
                ],
                [
                  'Warming up',
                  String(counts.warming_up),
                  warmingRows.length
                    ? `${warmingRows.map((r) => r.symbol).join(', ')} · history loading`
                    : '—',
                  'var(--sa-warning-text)',
                ],
              ] as const
            ).map(([label, value, sub, color]) => (
              <div
                key={label}
                style={{
                  flex: '1 1 130px',
                  padding: 'var(--sa-space-3) var(--sa-space-4)',
                  background: 'var(--sa-panel)',
                  border: '1px solid var(--sa-border)',
                  borderRadius: 'var(--sa-radius-lg)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                  minWidth: 0,
                }}
              >
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{label}</span>
                <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-2xl)', color }}>{value}</span>
                <span
                  title={sub}
                  style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                  {sub}
                </span>
              </div>
            ))}
          </section>

          {/* today's screen + how it works */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
            <Card
              label="Today’s screen"
              right={
                screen?.session_date
                  ? `${data.screen_time_ny} NY · ${screen.funnel[0]?.count ?? 0} → ${screen.eligible_total}`
                  : 'no screen yet'
              }
            >
              {screen && screen.funnel.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
                  {screen.funnel.map((step, index) => {
                    const total = screen.funnel[0]?.count || 1;
                    const pct = Math.round((step.count / total) * 1000) / 10;
                    const last = index === screen.funnel.length - 1;
                    const outText =
                      step.out.length === 0
                        ? null
                        : step.key === 'history'
                          ? `${step.out.length} warming up: ${step.out.slice(0, 4).join(', ')}${step.out.length > 4 ? ` +${step.out.length - 4}` : ''}`
                          : `${step.out.length} out: ${step.out.slice(0, 4).join(', ')}${step.out.length > 4 ? ` +${step.out.length - 4}` : ''}`;
                    return (
                      <div
                        key={step.key}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'minmax(0, 1fr) 44px',
                          gap: '4px 12px',
                          alignItems: 'center',
                        }}
                      >
                        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: '2px 12px', fontSize: 'var(--sa-text-md)' }}>
                          <span style={{ color: 'var(--sa-text-2)' }}>{funnelLabel(step, data)}</span>
                          {outText && (
                            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-warning-text)' }}>{outText}</span>
                          )}
                        </div>
                        <span style={{ gridRow: 'span 2', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-lg)', textAlign: 'right' }}>
                          {step.count}
                        </span>
                        <div style={{ height: 6, borderRadius: 3, background: 'var(--sa-raised)' }}>
                          <div
                            style={{
                              width: `${pct}%`,
                              height: 6,
                              borderRadius: 3,
                              background: last ? 'var(--sa-accent)' : 'var(--sa-text-faint)',
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                  <div style={{ display: 'flex', justifyContent: 'space-between', paddingTop: 10, borderTop: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)' }}>
                    <b style={{ fontWeight: 600 }}>Eligible for the next session</b>
                    <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-accent)' }}>{screen.eligible_total}</span>
                  </div>
                </div>
              ) : (
                <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
                  No screen yet — the first screen runs at {data.screen_time_ny} New York on the next trading day.
                </p>
              )}
            </Card>

            <Card label="How the screen works">
              <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
                <li>
                  A symbol drops out only after failing the screen on {data.thresholds.drop_after_failed_screens} trading days in a row. Until then it shows “eligible · 1 fail” or “2 fails”.
                </li>
                <li>
                  {data.preview
                    ? 'This list is a preview — cycles still trade the fixed list.'
                    : 'Cycles trade this list — the rank orders the model calls.'}
                </li>
                <li>
                  Rank is the return over the last {data.thresholds.lookback_sessions} sessions minus SPY’s, recomputed at the daily screen.
                </li>
              </ul>
            </Card>
          </div>

          {/* pool table */}
          <Card label="Pool" right="Sorted by rank">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'center' }}>
              <div role="group" aria-label="Filter the pool" style={{ display: 'inline-flex', flexWrap: 'wrap', padding: 3, background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)' }}>
                {TABS.map((t) => {
                  const count =
                    t === 'All'
                      ? (data.rows ?? []).length
                      : t === 'Eligible'
                        ? counts.eligible
                        : t === 'Held'
                          ? counts.held
                          : t === 'Excluded'
                            ? counts.excluded
                            : counts.warming_up;
                  const selected = tab === t;
                  return (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setTab(t)}
                      style={{
                        height: 36,
                        padding: '0 12px',
                        border: 0,
                        borderRadius: 'var(--sa-radius-sm)',
                        background: selected ? 'var(--sa-border-strong)' : 'transparent',
                        color: selected ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                        fontSize: 'var(--sa-text-md)',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                        cursor: 'pointer',
                      }}
                    >
                      {t} <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>{count}</span>
                    </button>
                  );
                })}
              </div>
              <input
                type="search"
                aria-label="Find a symbol"
                placeholder="Find a symbol"
                value={find}
                onChange={(e) => setFind(e.target.value)}
                style={{
                  height: 40,
                  boxSizing: 'border-box',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-bg)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-base)',
                  flex: '1 1 180px',
                  maxWidth: 260,
                  padding: '0 12px',
                }}
              />
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Sorted by rank</span>
            </div>

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 860, borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {(
                      [
                        ['Rank', 'Order in which candidates that pass the rules get a model call', 'left'],
                        ['Symbol', '', 'left'],
                        ['Sector', '', 'left'],
                        ['Price', '', 'right'],
                        [`Avg $ volume, ${data.thresholds.lookback_sessions} sessions ($M)`, '', 'right'],
                        [`vs SPY, ${data.thresholds.lookback_sessions} sessions`, 'Return over the last sessions minus SPY return', 'right'],
                        ['Status', '', 'left'],
                        ['', '', 'right'],
                      ] as const
                    ).map(([head, title, align]) => (
                      <th
                        key={head || 'actions'}
                        scope="col"
                        title={title || undefined}
                        style={{
                          textAlign: align as 'left' | 'right',
                          padding: '8px 10px',
                          fontSize: 'var(--sa-text-sm)',
                          fontWeight: 500,
                          color: 'var(--sa-text-muted)',
                          borderBottom: '1px solid var(--sa-border)',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {head}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {headRows.map(renderRow)}
                  {hiddenRows.length > 0 && (
                    <tr>
                      <td colSpan={8} style={{ padding: 0, borderBottom: '1px solid var(--sa-border)' }}>
                        <button
                          type="button"
                          className="sa-row"
                          onClick={() => setShowAllRows(true)}
                          style={{
                            width: '100%',
                            padding: '10px',
                            border: 0,
                            background: 'transparent',
                            color: 'var(--sa-text-muted)',
                            fontSize: 'var(--sa-text-md)',
                            textAlign: 'center',
                            cursor: 'pointer',
                          }}
                        >
                          {hiddenRows.length} more eligible symbols — show all
                        </button>
                      </td>
                    </tr>
                  )}
                  {tailRows.map(renderRow)}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={8} style={{ padding: '12px 10px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                        No symbols in this view.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', paddingTop: 4 }}>
              <input
                aria-label="Symbol to add"
                placeholder="Symbol, e.g. PANW"
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                style={{
                  height: 40,
                  boxSizing: 'border-box',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-bg)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-base)',
                  width: 170,
                  padding: '0 10px',
                  textTransform: 'uppercase',
                }}
              />
              <select
                aria-label="Sector"
                value={newSector}
                onChange={(e) => setNewSector(e.target.value)}
                style={{
                  height: 40,
                  boxSizing: 'border-box',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-bg)',
                  color: newSector ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                  fontSize: 'var(--sa-text-base)',
                  padding: '0 10px',
                  minWidth: 200,
                }}
              >
                <option value="">Sector…</option>
                {data.allowed_sectors.map((sector) => (
                  <option key={sector} value={sector}>
                    {sector}
                  </option>
                ))}
              </select>
              <Button onClick={addSymbol} disabled={busy || !newSymbol.trim() || !newSector}>
                Add to pool
              </Button>
              <Button variant="ghost" onClick={() => setImportOpen((o) => !o)} style={{ border: '1px solid var(--sa-border)' }}>
                Import CSV…
              </Button>
              <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                Pool limit {data.pool.max}. New symbols warm up first (history loads), then join at the next daily screen.
              </span>
            </div>

            {importOpen && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 'var(--sa-space-3)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)' }}>
                <label htmlFor="csv" style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  CSV rows: symbol,sector — one per line. All rows must be valid or nothing is imported.
                </label>
                <textarea
                  id="csv"
                  aria-label="CSV to import"
                  rows={5}
                  value={csv}
                  onChange={(e) => setCsv(e.target.value)}
                  placeholder={'PANW,Technology\nGE Vernova,Industrials'}
                  style={{
                    boxSizing: 'border-box',
                    width: '100%',
                    borderRadius: 'var(--sa-radius-md)',
                    border: '1px solid var(--sa-border-strong)',
                    background: 'var(--sa-bg)',
                    color: 'var(--sa-text)',
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-md)',
                    padding: 10,
                    resize: 'vertical',
                  }}
                />
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button onClick={importCsv} disabled={busy || !csv.trim()}>
                    Import
                  </Button>
                  <Button variant="ghost" onClick={() => setImportOpen(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

const td = (): React.CSSProperties => ({
  padding: '10px',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
  verticalAlign: 'middle',
});

const tdMono = (color?: string): React.CSSProperties => ({
  ...td(),
  fontFamily: 'var(--sa-font-mono)',
  textAlign: 'right',
  color: color ?? 'var(--sa-text)',
});
