import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import {
  getUniverse,
  getUniverseSync,
  postUniverseAdd,
  postUniverseImport,
  postUniverseRemove,
  postUniverseSyncRetry,
} from '../api/state';
import type {
  UniverseFunnelStep,
  UniverseResponse,
  UniverseRow,
  UniverseSyncJob,
  UniverseSyncResponse,
} from '../types/api';
import {
  amsTime,
  fmtDuration,
  isoMs,
  jobResultText,
  statusWord,
  syncAttention,
} from './universeFormat';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { SkeletonTable, SkeletonTiles } from '../components/skeletons';
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

const TABS = ['All', 'Eligible', 'Held', 'Excluded', 'Warming up', 'Ready'] as const;
type Tab = (typeof TABS)[number];

function inTab(row: UniverseRow, tab: Tab): boolean {
  switch (tab) {
    case 'Eligible':
      return row.status === 'eligible' || row.status === 'eligible_fails';
    case 'Held':
      return row.held;
    case 'Excluded':
      return row.status === 'excluded';
    case 'Warming up':
      return row.status === 'warming_up';
    case 'Ready':
      return row.status === 'ready';
    default:
      return true;
  }
}

function StatusChip({ row }: { row: UniverseRow }) {
  if (row.held) {
    if (!row.screened) return <Chip variant="info">held</Chip>;
    return <Chip variant="info">held · {statusWord(row)}</Chip>;
  }
  if (row.status === 'eligible' || row.status === 'eligible_fails') {
    return <Chip variant="positive">{statusWord(row)}</Chip>;
  }
  if (row.status === 'ready') return <Chip variant="info">ready</Chip>;
  if (row.status === 'warming_up') return <Chip variant="warning">warming up</Chip>;
  if (row.status === 'excluded') return <Chip variant="negative">excluded</Chip>;
  return <Chip variant="neutral">{row.status}</Chip>;
}

/** The two warm-up progress bars (daily bars, 5-min sessions) — item 077. */
function WarmBars({ row }: { row: UniverseRow }) {
  const p = row.progress;
  if (!p) return null;
  const bar = (label: string, have: number, need: number) => {
    const pct = need > 0 ? Math.min(100, (have / need) * 100) : 0;
    return (
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '84px minmax(60px, 1fr) auto',
          gap: '8px',
          alignItems: 'center',
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        <span>{label}</span>
        <div style={{ height: 4, borderRadius: 2, background: 'var(--sa-raised)' }}>
          <div
            className="sa-bar-fill"
            style={{ width: `${pct}%`, height: 4, borderRadius: 2, background: 'var(--sa-warning-text)' }}
          />
        </div>
        <span style={{ fontFamily: 'var(--sa-font-mono)' }}>
          {have} of {need}
        </span>
      </div>
    );
  };
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-1)',
        marginTop: 2,
        minWidth: 220,
      }}
    >
      {bar('daily bars', p.daily.have, p.daily.need)}
      {bar('5-min', p.m5.have, p.m5.need)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data sync (item 077): one quiet line while every job is fine, the job
// rows when something failed, is late or is running.

function SyncJobRow({
  job,
  sync,
  nowMs,
  onRetry,
  retrying,
}: {
  job: UniverseSyncJob;
  sync: UniverseSyncResponse;
  nowMs: number | null;
  onRetry: (job: string) => void;
  retrying: boolean;
}) {
  const dot =
    job.status === 'running'
      ? 'var(--sa-info)'
      : job.status === 'ok'
        ? 'var(--sa-positive)'
        : job.status === 'partial'
          ? 'var(--sa-warning-text)'
          : 'var(--sa-negative-text)';
  const resultTone =
    job.status === 'failed' || job.status === 'partial'
      ? 'var(--sa-negative-text)'
      : 'var(--sa-text-2)';
  let timeText: string;
  if (job.status === 'running' && job.started_at) {
    const startMs = isoMs(job.started_at);
    const elapsed = nowMs !== null && startMs !== null ? (nowMs - startMs) / 1000 : null;
    timeText =
      elapsed !== null && elapsed >= 0
        ? `running · ${fmtDuration(elapsed)} so far`
        : `running · started ${amsTime(job.started_at)}`;
  } else {
    timeText = `${amsTime(job.finished_at ?? job.started_at)} · ${fmtDuration(job.duration_seconds)}`;
  }
  const canRetry = job.status !== 'running' && job.failures.length > 0;
  return (
    <div className="sa-job" style={{ padding: '10px 0', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot, flex: '0 0 auto' }} />
        <span style={{ minWidth: 0 }}>
          <b style={{ fontWeight: 600 }}>{job.label}</b>
          <br />
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {job.schedule ?? ''}
            {job.late ? ' · late' : ''}
          </span>
        </span>
      </span>
      <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)', whiteSpace: 'nowrap' }}>
        {timeText}
      </span>
      <span className="sa-job-res" style={{ fontSize: 'var(--sa-text-sm)', color: resultTone, minWidth: 0 }}>
        {jobResultText(job, sync)}
      </span>
      <span>
        {canRetry && (
          <Button variant="default" onClick={() => onRetry(job.job)} busy={retrying} busyLabel="Retrying…" waitingFor="the sync" style={{ height: 32, padding: '0 16px', fontSize: 'var(--sa-text-base)' }}>
            Retry
          </Button>
        )}
      </span>
    </div>
  );
}

function SyncCard({
  sync,
  nowMs,
  onRetry,
  retryingJob,
}: {
  sync: UniverseSyncResponse;
  nowMs: number | null;
  onRetry: (job: string) => void;
  retryingJob: string | null;
}) {
  const attention = syncAttention(sync);
  const needsAttention = attention.failed + attention.running + attention.late > 0;
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? needsAttention;

  if (!open) {
    const finished = sync.jobs
      .map((j) => j.finished_at)
      .filter((v): v is string => Boolean(v))
      .sort();
    const last = finished.length > 0 ? finished[finished.length - 1] : null;
    return (
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '6px 14px',
          fontSize: 'var(--sa-text-md)',
          color: 'var(--sa-text-muted)',
        }}
      >
        <span>
          {sync.jobs.length === 0
            ? 'No sync runs recorded yet'
            : `All ${sync.jobs.length} jobs ran on time${last ? ` · last ${amsTime(last)}` : ''}`}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={() => setUserOpen(true)}
          style={{
            border: 0,
            background: 'transparent',
            color: 'var(--sa-accent)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: 'pointer',
            padding: '4px 0',
          }}
        >
          Show jobs
        </button>
      </div>
    );
  }

  const reasonText = [
    attention.failed > 0 ? `${attention.failed} failed` : null,
    attention.running > 0 ? `${attention.running} running` : null,
    attention.late > 0 ? `${attention.late} late` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h2
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 600,
            color: 'var(--sa-text-2)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--sa-label-tracking)',
          }}
        >
          Data sync
        </h2>
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {reasonText ? `open: ${reasonText}` : 'all jobs on time'}
          {' · '}
          <button
            type="button"
            onClick={() => setUserOpen(false)}
            style={{
              border: 0,
              background: 'transparent',
              color: 'var(--sa-accent)',
              fontSize: 'var(--sa-text-md)',
              fontWeight: 600,
              cursor: 'pointer',
              padding: 0,
            }}
          >
            Hide jobs
          </button>
        </div>
      </div>

      {sync.history && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            padding: '12px 14px',
            border: '1px solid var(--sa-warning-border)',
            background: 'var(--sa-warning-bg)',
            borderRadius: 'var(--sa-radius-md)',
          }}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: '6px 12px', fontSize: 'var(--sa-text-md)' }}>
            <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>
              Loading history · {sync.history.ready} of {sync.history.total ?? '?'} symbols ready
            </b>
            <span style={{ color: 'var(--sa-text-2)' }}>
              {sync.history.eta_seconds !== null ? `about ${fmtDuration(sync.history.eta_seconds)} left` : ''}
              {sync.max_requests ? `${sync.history.eta_seconds !== null ? ' · ' : ''}up to ${sync.max_requests} requests at once` : ''}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={sync.history.total ?? 1}
            aria-valuenow={sync.history.ready}
            aria-label="Symbols with their history loaded"
            style={{ height: 8, borderRadius: 4, background: 'var(--sa-raised)' }}
          >
            <div
              className="sa-bar-fill"
              style={{
                width: `${sync.history.total ? Math.min(100, (sync.history.ready / sync.history.total) * 100) : 0}%`,
                height: 8,
                borderRadius: 4,
                background: 'var(--sa-warning-text)',
              }}
            />
          </div>
        </div>
      )}

      {sync.jobs.length === 0 && (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          No sync runs recorded yet — jobs write their first row when they next run.
        </p>
      )}
      {sync.jobs.map((job) => (
        <SyncJobRow
          key={job.job}
          job={job}
          sync={sync}
          nowMs={nowMs}
          onRetry={onRetry}
          retrying={retryingJob === job.job}
        />
      ))}

      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
        Requests of one job run at the same time
        {sync.max_requests ? `, at most ${sync.max_requests} at once (concurrency.max_requests)` : ''}
        , and still within Alpaca's 150 calls per minute. Times in Amsterdam.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

export default function Universe() {
  const [data, setData] = useState<UniverseResponse | null>(null);
  const [sync, setSync] = useState<UniverseSyncResponse | null>(null);
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
  const [retryingJob, setRetryingJob] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const sectorFilter = searchParams.get('sector');

  const refresh = useCallback(() => {
    getUniverse()
      .then((d) => {
        setData(d);
        setErrors([]);
      })
      .catch((e) => setErrors(errorMessages(e)));
  }, []);

  const refreshSync = useCallback(() => {
    getUniverseSync()
      .then(setSync)
      .catch(() => {
        // The card is secondary: a failed sync read never blocks the page.
      });
  }, []);

  useEffect(() => {
    refresh();
    refreshSync();
  }, [refresh, refreshSync]);
  useLiveUpdates(['universe'], () => {
    refresh();
    refreshSync();
  });

  // Progress is written by the jobs at most once a second; poll faster
  // only while something is running so the bars move. The tick also
  // feeds the "running · N so far" labels (first tick lands within
  // 5 s; until then the row shows its start time).
  const anyRunning = sync?.jobs.some((j) => j.status === 'running') ?? false;
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(() => {
      refreshSync();
      setNowMs(Date.now());
    }, 5000);
    return () => clearInterval(timer);
  }, [anyRunning, refreshSync]);

  const rows = useMemo(() => {
    const all = (data?.rows ?? []).filter(
      (r) => inTab(r, tab) && (!sectorFilter || r.sector === sectorFilter),
    );
    const q = find.trim().toUpperCase();
    return q ? all.filter((r) => r.symbol.includes(q)) : all;
  }, [data, tab, find, sectorFilter]);

  // The pool table collapses its middle (design/screens/Universe.dc.html):
  // first rows, a "n more <filter> · show all" expander naming the active
  // filter and count, then the tail with the warming-up and excluded rows.
  const HEAD = 8;
  const TAIL = 4;
  const collapsible = !showAllRows && rows.length > HEAD + TAIL + 2;
  const headRows = collapsible ? rows.slice(0, HEAD) : rows;
  const hiddenRows = collapsible ? rows.slice(HEAD, -TAIL) : [];
  const tailRows = collapsible ? rows.slice(-TAIL) : [];
  const foldName =
    tab !== 'All'
      ? ` ${tab.toLowerCase()}`
      : sectorFilter
        ? ` in ${sectorFilter}`
        : find.trim()
          ? ` matching "${find.trim()}"`
          : hiddenRows.every(
                (r) => r.status === 'eligible' || r.status === 'eligible_fails',
              )
            ? ' eligible'
            : '';

  const setSector = (sector: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (sector) next.set('sector', sector);
        else next.delete('sector');
        return next;
      },
      { replace: true },
    );
    setShowAllRows(false);
  };

  if (!data) {
    if (errors.length > 0) {
      return (
        <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto' }}>
          <Banner variant="negative">{errors.join(' ')}</Banner>
        </div>
      );
    }
    return (
      <div
        role="status"
        aria-label="Loading universe"
        style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}
      >
        <span className="sa-sr">Loading universe</span>
        <div className="sa-loading">
          <SkeletonTiles count={3} />
          <SkeletonTable
            columns={[
              { label: 'Symbol' },
              { label: 'Sector' },
              { label: 'Price', align: 'right' },
              { label: 'Volume', align: 'right' },
            ]}
            track="1fr 140px 110px 130px"
            rows={8}
          />
        </div>
      </div>
    );
  }

  const screened = data.mode === 'screened';
  const counts = data.counts;
  const heldSet = new Set(data.held_symbols);
  const ams = amsterdamForNy(data.screen_time_ny);
  const screen = data.screen;
  const preScreen = !screen?.session_date;

  const allRows = data.rows ?? [];
  const excludedRows = allRows.filter((r) => r.status === 'excluded');
  const excludedReason =
    excludedRows
      .map((r) => r.failed_check)
      .find((k) => k && k in CHECK_LABEL) ?? null;
  const warmingRows = allRows.filter((r) => r.status === 'warming_up');
  const readyRows = allRows.filter((r) => r.status === 'ready');

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
    setConfirmRemove(null);
    run(
      () => postUniverseRemove(symbol),
      () => {
        setNotice(`${symbol} removed from the pool.`);
        refresh();
      },
    );
  };

  const retrySync = (job: string) => {
    setRetryingJob(job);
    setErrors([]);
    postUniverseSyncRetry(job)
      .then(() => {
        setNotice(`Retry started for ${job}.`);
        refreshSync();
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setRetryingJob(null));
  };

  const hintStyle: React.CSSProperties = {
    fontSize: 'var(--sa-text-sm)',
    color: 'var(--sa-text-faint)',
  };

  const renderRow = (r: UniverseRow) => {
    const vs = vsSpy(r.return_vs_spy);
    return (
      <Fragment key={r.symbol}>
        <tr>
          <td style={tdMono('var(--sa-text-muted)')}>
            {r.rank !== null ? r.rank : preScreen ? <span style={hintStyle}>after the first screen</span> : '—'}
          </td>
          <td style={{ ...td(), fontWeight: 600 }}>
            {r.symbol}
            <div
              title="Used to tell headlines about this company from the rest (about_symbol). From Alpaca's asset name, cleaned; universe.names overrides it."
              style={{ fontWeight: 400, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}
            >
              {r.name ?? 'name pending'}
            </div>
          </td>
          <td style={{ ...td(), minWidth: 150 }}>
            <StatusChip row={r} />
            {r.status === 'warming_up' && r.progress ? (
              <WarmBars row={r} />
            ) : (
              <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 4 }}>
                {r.detail}
                {r.held && r.qty > 0 ? `${r.detail ? ' · ' : ''}${r.qty} sh` : ''}
              </div>
            )}
          </td>
          <td style={{ ...td(), color: 'var(--sa-text-2)' }}>{r.sector || '—'}</td>
          <td style={tdMono()}>
            {r.last_close !== null
              ? usd(r.last_close, 2)
              : preScreen
                ? <span style={hintStyle}>after the first screen</span>
                : '—'}
          </td>
          <td style={tdMono()}>
            {r.avg_dollar_volume !== null
              ? dollarVolumeM(r.avg_dollar_volume)
              : preScreen
                ? <span style={hintStyle}>after the first screen</span>
                : '—'}
          </td>
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
            {r.return_vs_spy !== null
              ? vs.text
              : preScreen
                ? <span style={hintStyle}>after the first screen</span>
                : '—'}
          </td>
          <td style={{ ...td(), textAlign: 'right' }}>
            {r.in_pool && !r.held && r.symbol !== 'SPY' && (
              <button
                type="button"
                aria-label={`Remove ${r.symbol} from the pool`}
                title="Remove from the pool"
                disabled={busy}
                onClick={() => setConfirmRemove(r.symbol)}
                style={{
                  width: 32,
                  height: 32,
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
        {confirmRemove === r.symbol && (
          <tr>
            <td
              colSpan={8}
              style={{ padding: 0, borderBottom: '1px solid var(--sa-border)', background: 'var(--sa-raised)' }}
            >
              <div
                role="group"
                aria-label={`Confirm removal of ${r.symbol}`}
                style={{
                  position: 'sticky',
                  left: 10,
                  width: 'min(100%, calc(100vw - 100px))',
                  boxSizing: 'border-box',
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: '10px 14px',
                  fontSize: 'var(--sa-text-md)',
                  padding: '10px',
                }}
              >
                <span style={{ color: 'var(--sa-text)' }}>
                  Remove {r.symbol} from the pool? Its cached bars stay for 120 sessions.
                </span>
                <span style={{ flex: 1 }} />
                <Button
                  variant="ghost"
                  onClick={() => setConfirmRemove(null)}
                  style={{ border: '1px solid var(--sa-border)' }}
                  autoFocus
                >
                  Cancel
                </Button>
                <Button variant="danger" onClick={() => removeSymbol(r.symbol)} busy={busy} busyLabel="Removing…" waitingFor="the universe">
                  Remove
                </Button>
              </div>
            </td>
          </tr>
        )}
      </Fragment>
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

      {/* mode banner — the mode is said once, here */}
      <section
        aria-label="Universe mode"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '10px 16px',
          padding: '14px 18px',
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
                  {s.name ? `${s.name} · ` : ''}{s.sector || 'no sector'}
                </span>
                {heldSet.has(s.symbol) && <Chip variant="info">held</Chip>}
              </span>
            ))}
          </div>
        </Card>
      )}

      {data.pool && counts && (
        <>
          {/* summary tiles — a zero stays in the normal text colour;
              amber and red only appear above zero (item 077) */}
          <section aria-label="Universe summary" className="sa-tiles sa-tiles-6">
            <CardBoundary name="Universe summary">
            {(
              [
                ['Pool', String(data.pool.size), `limit ${data.pool.max}`, 'var(--sa-text)'],
                [
                  'Eligible',
                  String(counts.eligible),
                  data.preview ? 'preview' : 'next session',
                  counts.eligible > 0 ? 'var(--sa-accent)' : 'var(--sa-text)',
                ],
                ['Held', String(counts.held), data.held_symbols.join(', ') || '—', 'var(--sa-text)'],
                [
                  'Excluded',
                  String(counts.excluded),
                  excludedReason ? CHECK_LABEL[excludedReason] : '—',
                  counts.excluded > 0 ? 'var(--sa-negative-text)' : 'var(--sa-text)',
                ],
                [
                  'Warming up',
                  String(counts.warming_up),
                  warmingRows.length
                    ? `${warmingRows[0].symbol} · history loading`
                    : '—',
                  counts.warming_up > 0 ? 'var(--sa-warning-text)' : 'var(--sa-text)',
                ],
                [
                  'Ready',
                  String(counts.ready ?? 0),
                  readyRows.length && readyRows[0].next_screen
                    ? `${readyRows[0].symbol} · joins at ${readyRows[0].next_screen}`
                    : '—',
                  (counts.ready ?? 0) > 0 ? 'var(--sa-info)' : 'var(--sa-text)',
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
            </CardBoundary>
          </section>

          {/* Data sync next to Today's screen */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
            {sync && (
              <Card ariaLabel="Data sync" style={{ flex: '1 1 520px' }}>
                <SyncCard sync={sync} nowMs={nowMs} onRetry={retrySync} retryingJob={retryingJob} />
              </Card>
            )}

            <Card
              label="Today’s screen"
              right={
                screen?.session_date
                  ? `${data.screen_time_ny} NY · ${screen.funnel[0]?.count ?? 0} → ${screen.eligible_total}`
                  : 'no screen yet'
              }
              style={{ flex: '1 1 380px' }}
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

              {/* How the screen works — a fold, closed by default */}
              <details style={{ borderTop: '1px solid var(--sa-border)', paddingTop: 10 }}>
                <summary style={{ cursor: 'pointer', minHeight: 32, display: 'flex', alignItems: 'center', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                  ▸ How the screen works
                </summary>
                <div style={{ marginTop: 8 }}>
                  <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
                    <li>
                      A symbol drops out only after failing the screen on {data.thresholds.drop_after_failed_screens} trading days in a row. Until then it shows “eligible · 1 fail” or “2 fails”.
                    </li>
                    {!data.preview && (
                      <li>
                        Each cycle, held positions get a model call first. Candidates that pass the rules follow in rank order, up to {data.thresholds.max_calls_per_cycle ?? 10} calls, sent at the same time. The rest are stored as no_call (call cap).
                      </li>
                    )}
                    <li>
                      Rank is the return over the last {data.thresholds.lookback_sessions} sessions minus SPY’s, recomputed at the daily screen.
                    </li>
                    <li>
                      The company name under each symbol is what code uses to mark headlines as about the symbol. It comes from Alpaca's asset name; <span style={{ fontFamily: 'var(--sa-font-mono)' }}>universe.names</span> in config overrides it.
                    </li>
                  </ul>
                </div>
              </details>
            </Card>
          </div>

          {/* Pool by sector — click filters the pool (?sector= in the URL) */}
          {(data.sectors ?? []).length > 0 && (
            <Card
              label="Pool by sector"
              right={data.pool ? `${data.pool.size} in the pool` : undefined}
            >
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {(data.sectors ?? []).map((s) => {
                  const active = sectorFilter === s.sector;
                  return (
                    <button
                      key={s.sector}
                      type="button"
                      aria-pressed={active}
                      title={`Filter the pool to ${s.sector}`}
                      onClick={() => setSector(active ? null : s.sector)}
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 2,
                        alignItems: 'flex-start',
                        minWidth: 150,
                        padding: '8px 12px',
                        borderRadius: 'var(--sa-radius-md)',
                        border: `1px solid ${active ? 'var(--sa-accent-border)' : 'var(--sa-border)'}`,
                        background: active ? 'var(--sa-accent-bg)' : 'var(--sa-raised)',
                        color: 'var(--sa-text)',
                        fontSize: 'var(--sa-text-md)',
                        textAlign: 'left',
                        cursor: 'pointer',
                      }}
                    >
                      <b style={{ fontWeight: 600 }}>{s.sector}</b>
                      <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                        {s.eligible} of {s.pool} eligible · rank {s.rank ?? '—'}
                        {s.rank !== null && s.rank_total ? `/${s.rank_total}` : ''}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                Rank: the sector ETF's strength against SPY at the last cycle, 1 of {data.sectors?.[0]?.rank_total ?? '?'} is the strongest. Click a sector to filter the pool.
              </div>
            </Card>
          )}

          {/* pool table */}
          <Card label="Pool" right={sectorFilter ? `sector: ${sectorFilter} · clear` : 'Sorted by rank · symbols without a rank last'}>
            {sectorFilter && (
              <button
                type="button"
                onClick={() => setSector(null)}
                style={{
                  alignSelf: 'flex-start',
                  border: '1px solid var(--sa-border)',
                  background: 'transparent',
                  color: 'var(--sa-text-2)',
                  borderRadius: 'var(--sa-radius-md)',
                  padding: '4px 12px',
                  fontSize: 'var(--sa-text-md)',
                  cursor: 'pointer',
                }}
              >
                Sector: {sectorFilter} ×
              </button>
            )}
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
                            : t === 'Warming up'
                              ? counts.warming_up
                              : (counts.ready ?? 0);
                  const selected = tab === t;
                  return (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => {
                        setTab(t);
                        setShowAllRows(false);
                      }}
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
                onChange={(e) => {
                  setFind(e.target.value);
                  setShowAllRows(false);
                }}
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
            </div>

            <div style={{ overflowX: 'auto', minWidth: 0 }}>
              <table style={{ width: '100%', minWidth: 860, borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {(
                      [
                        ['Rank', 'Order in which candidates that pass the rules get a model call', 'left'],
                        ['Symbol', '', 'left'],
                        ['Status', '', 'left'],
                        ['Sector', '', 'left'],
                        ['Price', '', 'right'],
                        [`Avg $ volume, ${data.thresholds.lookback_sessions} sessions ($M)`, '', 'right'],
                        [`vs SPY, ${data.thresholds.lookback_sessions} sessions`, 'Return over the last sessions minus SPY return', 'right'],
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
                          {hiddenRows.length} more{foldName} · show all
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
              <Button
                onClick={addSymbol}
                busy={busy}
                busyLabel="Adding…"
                waitingFor="the universe"
                disabled={!busy && (!newSymbol.trim() || !newSector)}
              >
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
                  <Button onClick={importCsv} busy={busy} busyLabel="Importing…" waitingFor="the universe" disabled={!busy && !csv.trim()}>
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
