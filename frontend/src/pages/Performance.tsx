import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import { getPerformance, getPerformanceTails } from '../api/state';
import { useApiResource } from '../api/useResource';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { EquityCurveChart } from '../components/EquityCurveChart';
import type {
  CurveDrawdown,
  CurveExit,
  EquityCurvePoint,
} from '../components/EquityCurveChart';
import { SegmentedControl } from '../components/SegmentedControl';
import { StatCard } from '../components/StatCard';
import { TailsCard } from '../components/TailsCard';
import { ErrorBanner } from '../components/cardState';
import {
  SkeletonChart,
  SkeletonTable,
  SkeletonTiles,
} from '../components/skeletons';
import { displayFullDate, fmtDayMonthIn, sessionDayShort } from '../format';
import { EXIT_REASONS, labelFor } from '../labels';
import type {
  ClosedTradeRow,
  PerformanceAggRow,
} from '../types/api';

const mono: React.CSSProperties = {
  fontFamily: 'var(--sa-font-mono)',
};

const positive = 'var(--sa-positive)';
const negative = 'var(--sa-negative-text)';
const muted = 'var(--sa-text-muted)';

function signedMoney(value: string | null | undefined, symbol = '$'): string {
  if (value == null) return '—';
  const n = Number(value);
  const abs = `${symbol}${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (n > 0) return `+${abs}`;
  if (n < 0) return `−${abs}`;
  return abs;
}

function signColor(value: string | null | undefined): string {
  if (value == null) return 'var(--sa-text)';
  const n = Number(value);
  if (n > 0) return positive;
  if (n < 0) return negative;
  return 'var(--sa-text)';
}

function signedPct(value: string | null | undefined): string {
  if (value == null) return '—';
  const n = Number(value);
  const abs = `${Math.abs(n).toFixed(2)}%`;
  if (n > 0) return `+${abs}`;
  if (n < 0) return `−${abs}`;
  return abs;
}

function signedR(value: string | null | undefined): string {
  if (value == null) return '—';
  const n = Number(value);
  if (n > 0) return `+${n.toFixed(2)}R`;
  if (n < 0) return `−${Math.abs(n).toFixed(2)}R`;
  return `${n.toFixed(2)}R`;
}

/** "13 Oct" in Amsterdam time. */
const dayLabel = (iso: string | null | undefined): string =>
  iso ? fmtDayMonthIn(iso) || '—' : '—';

/** "Monday 5 October" in Amsterdam time. */
const longDayLabel = displayFullDate;

/** Compact regime tag for the closed-trades column ("up · normal"). */
function shortRegime(label: string | null): string {
  if (!label) return '—';
  return label
    .replace('normal vol', 'normal')
    .replace(' · any', '')
    .toLowerCase();
}

/** "sideways" / "down · normal or low" for the folded-regimes line. */
function foldRegime(label: string): string {
  if (label === 'Sideways · any') return 'sideways';
  return label.charAt(0).toLowerCase() + label.slice(1);
}

const REGIME_BLOCKS_ENTRIES = 'Down · high vol';

function StatTip({ children, title }: { children: string; title: string }) {
  return (
    <span
      title={title}
      style={{
        textDecoration: 'underline dotted',
        textUnderlineOffset: '3px',
        cursor: 'help',
        alignSelf: 'flex-start',
      }}
    >
      {children}
    </span>
  );
}

/** The tile's two lines under the value (item 076): a plain-words line,
 * then the small detail line. */
function TileLines({ plain, small }: { plain: string; small: string }) {
  return (
    <>
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.4 }}>
        {plain}
      </div>
      <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>{small}</div>
    </>
  );
}

function AggTable({ rows, stale }: { rows: PerformanceAggRow[]; stale?: boolean }) {
  return (
    <div className={stale ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
      <table style={{ width: '100%', minWidth: '420px' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {['', 'P&L', 'Avg R', 'Trades', 'Won'].map((h, i) => (
              <th
                key={i}
                scope="col"
                style={{
                  padding: 'var(--sa-space-2) 10px',
                  textAlign: i === 0 ? 'left' : 'right',
                  fontSize: 'var(--sa-text-sm)',
                  color: muted,
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} style={{ borderBottom: '1px solid var(--sa-border)' }}>
              <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)', fontWeight: row.trades ? 600 : 400 }}>
                {row.label}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(row.pnl) }}>
                {signedMoney(row.pnl)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {signedR(row.avg_r)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>{row.trades}</td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {row.won ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The R cell's bar: from −1R to the trade's R, capped at 2R wide. */
function RBar({ r }: { r: string | null }) {
  if (r == null) return <span style={{ color: muted }}>—</span>;
  const n = Number(r);
  const clamped = Math.max(-1, Math.min(2, n));
  const pos = ((clamped + 1) / 3) * 60;
  const zero = 20;
  const left = Math.min(pos, zero);
  const width = Math.abs(pos - zero);
  const color = n > 0 ? positive : n < 0 ? negative : muted;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
      <span
        aria-hidden="true"
        style={{
          position: 'relative',
          width: 60,
          height: 6,
          background: 'var(--sa-border)',
          borderRadius: 3,
          flex: '0 0 60px',
        }}
      >
        <span
          style={{
            position: 'absolute',
            left: zero - 0.5,
            top: -2,
            width: 1,
            height: 10,
            background: muted,
          }}
        />
        {width > 0 && (
          <span
            style={{
              position: 'absolute',
              top: 0,
              height: 6,
              borderRadius: 3,
              background: color,
              left,
              width,
            }}
          />
        )}
      </span>
      <span style={{ color, ...mono }}>{signedR(r)}</span>
    </span>
  );
}

const CLOSED_PAGE = 20;

function ClosedTradesTable({
  trades,
  stale,
}: {
  trades: ClosedTradeRow[];
  stale?: boolean;
}) {
  if (trades.length === 0) {
    return (
      <div style={{ color: muted, fontSize: 'var(--sa-text-md)' }}>
        No closed trades match.
      </div>
    );
  }
  return (
    <div className={stale ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
      <table style={{ width: '100%', minWidth: '1000px' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {[
              'Symbol',
              'R',
              'Adjusted',
              'Exit',
              'Dates',
              'Qty',
              'Entry',
              'Exit price',
              'P&L (paper)',
              'Regime at entry',
            ].map((h, i) => (
              <th
                key={h}
                scope="col"
                style={{
                  padding: 'var(--sa-space-2) 10px',
                  textAlign: i === 0 || i === 1 || i === 3 || i === 4 || i === 9 ? 'left' : 'right',
                  fontSize: 'var(--sa-text-sm)',
                  color: muted,
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} style={{ borderBottom: '1px solid var(--sa-border)' }}>
              <td style={{ padding: '10px', fontWeight: 600 }}>{t.symbol}</td>
              <td style={{ padding: '10px' }}>
                <RBar r={t.r} />
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(t.adjusted) }}>
                {signedMoney(t.adjusted)}
              </td>
              <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)' }}>
                {labelFor(EXIT_REASONS, t.exit_reason)}
              </td>
              <td style={{ padding: '10px', ...mono, fontSize: 'var(--sa-text-md)' }}>
                {dayLabel(t.opened_at)} → {dayLabel(t.closed_at)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>{t.qty}</td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {Number(t.entry).toFixed(2)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {t.exit === null ? '—' : Number(t.exit).toFixed(2)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(t.pnl) }}>
                {signedMoney(t.pnl)}
              </td>
              <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)', color: muted }}>
                {shortRegime(t.regime)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const ANCHOR: React.CSSProperties = {
  display: 'block',
  height: 0,
  scrollMarginTop: 16,
};

export default function Performance() {
  const [runId, setRunId] = useState<number | undefined>(undefined);
  const [currency, setCurrency] = useState<'USD' | 'EUR'>('USD');
  const [searchParams, setSearchParams] = useSearchParams();
  const [showAll, setShowAll] = useState(false);

  const res = useApiResource(
    useCallback((signal?: AbortSignal) => getPerformance(runId, signal), [runId]),
    [runId],
  );
  const data = res.data;
  useLiveUpdates(['performance'], res.reload);

  // Winners and tails: same run as the rest of the page; latest request
  // wins when the run changes quickly.
  const tails = useApiResource(
    useCallback(() => getPerformanceTails(runId), [runId]),
    [runId],
  );
  useLiveUpdates(['performance'], tails.reload);

  const run = data?.run;
  const summary = data?.summary;
  const curve = data?.curve;
  const eurAvailable = (curve?.points ?? []).some((p) => p.eur_usd !== null);
  const chartCurrency = eurAvailable ? currency : 'USD';
  const symbol = chartCurrency === 'EUR' ? '€' : '$';

  const chartPoints = useMemo((): EquityCurvePoint[] => {
    if (!run || !curve) return [];
    const start = Number(run.starting_equity);
    const out: EquityCurvePoint[] = [
      { date: curve.start_label, equity: start, benchmark: start },
    ];
    let fx = 1;
    for (const p of curve.points) {
      if (p.eur_usd !== null) fx = Number(p.eur_usd);
      const divisor = chartCurrency === 'EUR' ? fx : 1;
      const point: EquityCurvePoint = {
        date: p.date,
        equity: Number(p.equity) / divisor,
        benchmark: p.benchmark === null ? null : Number(p.benchmark) / divisor,
      };
      // A snapshot on the run's start day replaces the synthetic start point
      // — the chart needs unique dates.
      if (out[out.length - 1].date === p.date) out[out.length - 1] = point;
      else out.push(point);
    }
    return out;
  }, [run, curve, chartCurrency]);

  const curveExits = useMemo((): CurveExit[] => {
    return (data?.exits ?? []).map((e) => ({
      date: e.date,
      symbol: e.symbol,
      r: e.r,
      won: e.won,
    }));
  }, [data?.exits]);

  const curveDrawdown = useMemo((): CurveDrawdown | null => {
    const dd = data?.drawdown;
    return dd ? { peak_date: dd.peak_date, low_date: dd.low_date, pct: dd.pct } : null;
  }, [data?.drawdown]);

  const lastPoint = chartPoints[chartPoints.length - 1];
  const money = (v: number | null | undefined) =>
    v == null
      ? '—'
      : `${symbol}${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const open = data?.open;
  const closedCount = data?.closed_trades?.length ?? 0;
  const closedRight = open && open.count > 0
    ? `${closedCount} closed · ${open.count} open (${open.symbols.join(', ')}: ${signedMoney(open.unrealized)} unrealized)`
    : `${closedCount} closed`;

  // ---- tiles' plain-words lines -------------------------------------
  const winRate = summary?.win_rate_pct;
  const dividends = Number(summary?.dividends_usd ?? 0);
  const closed = summary?.closed ?? 0;
  const wins = summary?.wins ?? 0;
  const avgR = summary?.avg_r == null ? null : Number(summary.avg_r);
  const ddPct = Number(summary?.max_drawdown_pct ?? 0);
  const vsSpy = summary?.vs_spy_pct == null ? null : Number(summary.vs_spy_pct);
  const retPct = summary?.return_pct == null ? null : Number(summary.return_pct);

  const returnPlain =
    retPct == null
      ? 'No equity readings yet.'
      : retPct > 0
        ? 'The account grew by this much since the start.'
        : retPct < 0
          ? 'The account is down by this much since the start.'
          : 'The account is unchanged since the start.';
  const vsSpyPlain =
    vsSpy == null
      ? 'No SPY comparison yet.'
      : vsSpy > 0
        ? 'Ahead of simply buying SPY on day one.'
        : vsSpy < 0
          ? 'Behind simply buying SPY on day one.'
          : 'Level with simply buying SPY on day one.';
  const avgRPlain =
    avgR == null
      ? 'No scored trades yet.'
      : avgR > 0
        ? `A trade earned ${Math.abs(avgR).toFixed(2)} times what it risked, on average.`
        : avgR < 0
          ? `A trade lost ${Math.abs(avgR).toFixed(2)} times what it risked, on average.`
          : 'A trade earned nothing on average.';

  // ---- closed trades: filters in the URL, 20 rows then "Show all n" ---
  const symbolFilter = searchParams.get('symbol') ?? '';
  const exitFilter = searchParams.get('exit') ?? '';
  const filterKey = `${runId ?? ''}|${symbolFilter}|${exitFilter}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (prevFilterKey !== filterKey) {
    setPrevFilterKey(filterKey);
    setShowAll(false);
  }

  const setFilter = (key: 'symbol' | 'exit', value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const exitOptions = useMemo(() => {
    const codes = new Set<string>();
    for (const t of data?.closed_trades ?? []) {
      if (t.exit_reason) codes.add(t.exit_reason);
    }
    return [...codes].sort();
  }, [data?.closed_trades]);

  const filteredTrades = useMemo(() => {
    const q = symbolFilter.trim().toUpperCase();
    return (data?.closed_trades ?? []).filter(
      (t) =>
        (!q || t.symbol.toUpperCase().includes(q)) &&
        (!exitFilter || t.exit_reason === exitFilter),
    );
  }, [data?.closed_trades, symbolFilter, exitFilter]);
  const visibleTrades = showAll
    ? filteredTrades
    : filteredTrades.slice(0, CLOSED_PAGE);

  // ---- by regime fold -------------------------------------------------
  const emptyRegimes = data?.empty_regimes ?? [];
  const foldList = emptyRegimes.filter((r) => r !== REGIME_BLOCKS_ENTRIES);
  const blocksNote = emptyRegimes.includes(REGIME_BLOCKS_ENTRIES);

  const minTrades = summary?.tail_min_trades;
  const smallSample =
    minTrades != null && closed < minTrades;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 'var(--sa-space-3)' }}>
        {run && (
          <span style={{ fontSize: 'var(--sa-text-md)', color: muted }}>
            Paper run since {longDayLabel(run.started_at)} · {run.sessions}{' '}
            {run.sessions === 1 ? 'session' : 'sessions'}
            {!run.current && ' · earlier run'}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {(data?.runs.length ?? 0) > 1 && (
          <select
            aria-label="Run"
            value={run?.id ?? ''}
            onChange={(e) => setRunId(Number(e.target.value))}
            style={{
              height: '32px',
              padding: '0 var(--sa-space-3)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              borderRadius: 'var(--sa-radius-sm)',
              color: 'var(--sa-text)',
              fontSize: 'var(--sa-text-sm)',
            }}
          >
            {data?.runs.map((r, i) => (
              <option key={r.id} value={r.id}>
                Run since {dayLabel(r.started_at)}{i === 0 ? ' (current)' : ''}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Phone only (item 076): jump links down the long page. */}
      <nav
        aria-label="On this page"
        className="sa-phone-only"
        style={{
          gap: '8px',
          overflowX: 'auto',
          paddingBottom: 2,
          alignItems: 'center',
        }}
      >
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap' }}>
          On this page
        </span>
        {(
          [
            ['Curve', '#curve'],
            ['By regime and symbol', '#by'],
            ['Winners and tails', '#tails'],
            ['Decisions', '#decisions'],
            ['Closed trades', '#trades'],
          ] as const
        ).map(([label, href]) => (
          <a
            key={href}
            href={href}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: '36px',
              padding: '0 var(--sa-space-3)',
              borderRadius: '18px',
              border: '1px solid var(--sa-border)',
              background: 'var(--sa-raised)',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              textDecoration: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </a>
        ))}
      </nav>

      {data === null && !res.loaded && res.failure === null && (
        <div className="sa-loading" aria-hidden="true">
          <SkeletonTiles count={6} />
          <Card ariaLabel="Equity curve">
            <SkeletonChart height={300} />
          </Card>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)' }}>
            {[0, 1].map((i) => (
              <Card key={i} ariaLabel="Table" style={{ flex: '1 1 440px' }}>
                <SkeletonTable
                  columns={['', 'P&L', 'Avg R', 'Trades', 'Won'].map((label, j) => ({
                    label,
                    align: j === 0 ? 'left' : 'right',
                  }))}
                  rows={5}
                  track="1.4fr 0.8fr 0.7fr 0.7fr 0.6fr"
                />
              </Card>
            ))}
          </div>
        </div>
      )}

      {data === null && res.failure !== null && (
        <ErrorBanner
          what="performance"
          failure={res.failure}
          updatedAt={res.updatedAt}
          onRetry={res.reload}
        />
      )}

      {data?.empty && (
        <Card label="Performance">
          <div style={{ color: muted, fontSize: 'var(--sa-text-md)' }}>
            No paper run yet — this page fills in once the first run starts.
          </div>
        </Card>
      )}

      {data && !data.empty && summary && (
        <>
          {/* Summary sentence + the page's one small-sample banner. */}
          <section aria-label="Summary" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {summary.sentence && (
              <p style={{ margin: 0, fontSize: '15px', lineHeight: 1.6, color: 'var(--sa-text)', maxWidth: 980 }}>
                {summary.sentence}
              </p>
            )}
            {smallSample && minTrades != null && (
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px 12px',
                  alignItems: 'center',
                  padding: '10px 12px',
                  border: '1px solid var(--sa-warning-border)',
                  background: 'var(--sa-warning-bg)',
                  borderRadius: 'var(--sa-radius-md)',
                  fontSize: 'var(--sa-text-md)',
                }}
              >
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    height: 24,
                    padding: '0 8px',
                    borderRadius: 6,
                    ...mono,
                    fontSize: 'var(--sa-text-sm)',
                    whiteSpace: 'nowrap',
                    color: 'var(--sa-warning-text)',
                    border: '1px solid var(--sa-warning-border)',
                  }}
                >
                  {closed} of {minTrades} trades
                </span>
                <span style={{ color: 'var(--sa-text-2)' }}>
                  Too few closed trades to judge the strategy. Every number on
                  this page can still swing with one trade; {minTrades} is the
                  first useful mark.
                </span>
              </div>
            )}
          </section>

          <section aria-label="Key numbers" className="sa-tiles sa-tiles-6">
            <CardBoundary name="Performance summary">
            <StatCard
              label={
                <StatTip title="Change in equity since the run started, as Alpaca reports it (paper fills, no costs).">
                  Return
                </StatTip>
              }
              value={signedPct(summary.return_pct)}
              valueColor={signColor(summary.return_pct)}
              sub={
                <TileLines
                  plain={returnPlain}
                  small={`${signedMoney(summary.return_usd)}${
                    summary.return_eur !== null
                      ? ` · ${signedMoney(summary.return_eur, '€')}`
                      : ''
                  }`}
                />
              }
            />
            <StatCard
              label={
                <StatTip title="Agent return minus the return of buying SPY on the run's first day and holding it.">
                  vs SPY hold
                </StatTip>
              }
              value={signedPct(summary.vs_spy_pct)}
              valueColor={signColor(summary.vs_spy_pct)}
              sub={
                <TileLines
                  plain={vsSpyPlain}
                  small={
                    summary.spy_return_pct !== null
                      ? `SPY ${signedPct(summary.spy_return_pct)} over the same days`
                      : 'no SPY baseline yet'
                  }
                />
              }
            />
            <StatCard
              label={
                <StatTip title="Paper fills ignore slippage and fees. This subtracts the assumed slippage on every fill and adds dividends received.">
                  Adjusted P&L
                </StatTip>
              }
              value={signedMoney(summary.adjusted_usd)}
              valueColor={signColor(summary.adjusted_usd)}
              sub={
                <TileLines
                  plain="What it would be after realistic costs."
                  small={`${summary.slippage_pct}% slippage per side · ${
                    dividends === 0
                      ? 'no dividends yet'
                      : `${signedMoney(summary.dividends_usd)} dividends`
                  }`}
                />
              }
            />
            <StatCard
              label={
                <StatTip title="Share of closed trades with a positive adjusted result.">
                  Win rate
                </StatTip>
              }
              value={winRate === null ? '—' : `${Math.round(Number(winRate))}%`}
              sub={
                <TileLines
                  plain={
                    closed > 0
                      ? `${wins} of ${closed} closed trades made money.`
                      : 'No closed trades yet.'
                  }
                  small="closed trades only"
                />
              }
            />
            <StatCard
              label={
                <StatTip title="R is what a trade risks: entry minus stop, times the shares. A stop-out is about −1R.">
                  Average R
                </StatTip>
              }
              value={signedR(summary.avg_r)}
              sub={
                <TileLines plain={avgRPlain} small="per closed trade" />
              }
            />
            <StatCard
              label={
                <StatTip title="Largest drop of equity from a previous high, using daily closes. At the kill percentage the kill switch turns on and everything is sold.">
                  Max drawdown
                </StatTip>
              }
              value={(() => {
                const dd = Math.abs(ddPct).toFixed(2);
                return dd === '0.00' ? '0.00%' : `−${dd}%`;
              })()}
              valueColor={ddPct > 0 ? negative : 'var(--sa-text)'}
              sub={
                <TileLines
                  plain={
                    ddPct > 0
                      ? 'The deepest fall from a high so far.'
                      : 'No fall from a high yet.'
                  }
                  small={`close to close · kill switch at −${summary.kill_pct}%`}
                />
              }
            />
            </CardBoundary>
          </section>

          {curve && chartPoints.length > 0 && (
            <>
              <span id="curve" style={ANCHOR} />
              <Card
                label={`Equity vs ${curve.benchmark} buy-and-hold`}
                refreshing={res.refreshing}
                right={
                  <SegmentedControl
                    label="Currency"
                    options={['USD', 'EUR']}
                    value={chartCurrency}
                    onChange={(o) => setCurrency(o as 'USD' | 'EUR')}
                  />
                }
              >
                <EquityCurveChart
                  points={chartPoints}
                  currency={chartCurrency}
                  exits={curveExits}
                  drawdown={curveDrawdown}
                />
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 18px', fontSize: 'var(--sa-text-sm)', color: muted }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 16, height: 2, background: '#F2B94B' }} />
                    Agent equity{' '}
                    <span style={{ ...mono, color: 'var(--sa-text-2)' }}>
                      {money(lastPoint?.equity)}
                    </span>
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 16, height: 0, borderTop: '2px dashed #98A2AF' }} />
                    {curve.benchmark} bought {dayLabel(curve.start_label)}{' '}
                    <span style={{ ...mono, color: 'var(--sa-text-2)' }}>
                      {money(lastPoint?.benchmark)}
                    </span>
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: '50%',
                        border: '2px solid var(--sa-positive)',
                        boxSizing: 'border-box',
                      }}
                    />
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: '50%',
                        border: '2px solid var(--sa-negative)',
                        boxSizing: 'border-box',
                      }}
                    />
                    Trade exit, won or lost
                  </span>
                  {curveDrawdown && Number(curveDrawdown.pct) > 0 && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <span
                        style={{
                          width: 14,
                          height: 10,
                          background: 'rgba(255,99,105,0.12)',
                          border: '1px solid rgba(255,99,105,0.35)',
                          boxSizing: 'border-box',
                        }}
                      />
                      Deepest drawdown
                    </span>
                  )}
                  <span style={{ flex: 1 }} />
                  <span>
                    {chartCurrency === 'EUR'
                      ? 'EUR at each day’s ECB rate; the account itself is in USD'
                      : `Start = ${dayLabel(curve.start_label)} open, then daily closes`}
                  </span>
                </div>
                <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
                  {curve.benchmark} price only, dividends excluded
                </div>
              </Card>
            </>
          )}

          <span id="by" style={ANCHOR} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)' }}>
            <div style={{ flex: '1 1 440px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              <Card label="By regime" refreshing={res.refreshing}>
                <AggTable rows={data.by_regime ?? []} stale={res.refreshing} />
                {foldList.length > 0 && (
                  <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                    No closed trades yet in: {foldList.map(foldRegime).join(', ')}.
                    {blocksNote && ' Down · high vol blocks new entries, so it never has trades.'}
                  </div>
                )}
                {closed < 30 && (
                  <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
                    {closed} {closed === 1 ? 'trade is' : 'trades are'} far too few to judge any regime.
                  </div>
                )}
              </Card>
            </div>
            <div style={{ flex: '1 1 440px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              <Card label="By symbol" refreshing={res.refreshing}>
                <AggTable rows={data.by_symbol ?? []} stale={res.refreshing} />
                <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
                  Closed trades only, best first.
                  {open && open.count > 0 &&
                    ` ${open.symbols.join(' and ')} ${open.count === 1 ? 'is' : 'are'} still open.`}{' '}
                  Symbols without a closed trade are not listed.
                </div>
              </Card>
            </div>
          </div>

          <span id="tails" style={ANCHOR} />
          <TailsCard res={tails} />

          <span id="decisions" style={ANCHOR} />
          <Card label="Decisions and outcomes" refreshing={res.refreshing}>
            <div className={res.refreshing ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
              <table style={{ width: '100%', minWidth: '560px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                    {['Decision', 'Price after 1 session', 'Count', 'Read'].map((h, i) => (
                      <th
                        key={h}
                        scope="col"
                        style={{
                          padding: 'var(--sa-space-2) 10px',
                          textAlign: i === 1 || i === 2 ? 'right' : 'left',
                          fontSize: 'var(--sa-text-sm)',
                          color: muted,
                          fontWeight: 500,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data.decision_outcomes ?? []).map((row) => (
                    <tr key={row.key} style={{ borderBottom: '1px solid var(--sa-border)' }}>
                      <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)' }}>{row.label}</td>
                      <td
                        style={{
                          padding: '10px',
                          textAlign: 'right',
                          ...mono,
                          color:
                            row.key === 'enter' && row.avg_1session_pct !== null
                              ? signColor(row.avg_1session_pct)
                              : muted,
                        }}
                      >
                        {row.avg_1session_pct === null ? (
                          <span style={{ fontFamily: 'inherit' }}>
                            waiting
                            {row.first_price_date
                              ? ` · first prices ${sessionDayShort(row.first_price_date)}`
                              : ' for the first prices'}
                          </span>
                        ) : (
                          `${signedPct(row.avg_1session_pct)} avg`
                        )}
                      </td>
                      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>{row.count}</td>
                      <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)' }}>{row.read}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
              Every decision is scored, skips included, so you can see whether the agent
              passes on good trades.
            </div>
          </Card>

          <span id="trades" style={ANCHOR} />
          <Card label="Closed trades" right={closedRight} refreshing={res.refreshing}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
              <input
                type="search"
                aria-label="Find a symbol"
                placeholder="Find a symbol"
                value={symbolFilter}
                onChange={(e) => setFilter('symbol', e.target.value)}
                style={{
                  height: '38px',
                  boxSizing: 'border-box',
                  padding: '0 12px',
                  borderRadius: '8px',
                  border: '1px solid var(--sa-border)',
                  background: 'var(--sa-bg)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-base)',
                  width: 200,
                }}
              />
              <select
                aria-label="Exit reason"
                value={exitFilter}
                onChange={(e) => setFilter('exit', e.target.value)}
                style={{
                  height: '38px',
                  padding: '0 10px',
                  borderRadius: '8px',
                  border: '1px solid var(--sa-border)',
                  background: 'var(--sa-bg)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-base)',
                }}
              >
                <option value="">Any exit</option>
                {exitOptions.map((code) => (
                  <option key={code} value={code}>
                    {labelFor(EXIT_REASONS, code)}
                  </option>
                ))}
              </select>
              <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
                newest first · {showAll ? `${filteredTrades.length} shown` : `${Math.min(filteredTrades.length, CLOSED_PAGE)} of ${filteredTrades.length}`}
              </span>
            </div>
            <ClosedTradesTable trades={visibleTrades} stale={res.refreshing} />
            {!showAll && filteredTrades.length > CLOSED_PAGE && (
              <div>
                <button
                  type="button"
                  onClick={() => setShowAll(true)}
                  style={{
                    height: '36px',
                    padding: '0 14px',
                    borderRadius: '8px',
                    border: '1px solid var(--sa-border)',
                    background: 'var(--sa-raised)',
                    color: 'var(--sa-text)',
                    fontSize: 'var(--sa-text-md)',
                    cursor: 'pointer',
                  }}
                >
                  Show all {filteredTrades.length}
                </button>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
