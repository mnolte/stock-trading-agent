import { useCallback, useMemo, useState } from 'react';

import { useLiveUpdates } from '../api/liveContext';
import { getPerformance, getPerformanceTails } from '../api/state';
import { useApiResource } from '../api/useResource';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { EquityCurveChart } from '../components/EquityCurveChart';
import type { EquityCurvePoint } from '../components/EquityCurveChart';
import { SegmentedControl } from '../components/SegmentedControl';
import { StatCard } from '../components/StatCard';
import { TailsCard } from '../components/TailsCard';
import { ErrorBanner } from '../components/cardState';
import {
  SkeletonChart,
  SkeletonTable,
  SkeletonTiles,
} from '../components/skeletons';
import { displayFullDate, fmtDayMonthIn } from '../format';
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

function AggTable({ rows, stale }: { rows: PerformanceAggRow[]; stale?: boolean }) {
  return (
    <div className={stale ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
      <table style={{ width: '100%', minWidth: '420px' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {['', 'Trades', 'Won', 'P&L', 'Avg R'].map((h, i) => (
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
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>{row.trades}</td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {row.won ?? '—'}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(row.pnl) }}>
                {signedMoney(row.pnl)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
                {signedR(row.avg_r)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClosedTradesTable({ trades, stale }: { trades: ClosedTradeRow[]; stale?: boolean }) {
  if (trades.length === 0) {
    return (
      <div style={{ color: muted, fontSize: 'var(--sa-text-md)' }}>
        No closed trades yet.
      </div>
    );
  }
  return (
    <div className={stale ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
      <table style={{ width: '100%', minWidth: '900px' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {[
              'Dates',
              'Symbol',
              'Qty',
              'Entry',
              'Exit',
              'P&L',
              'Adjusted',
              'R',
              'Exit',
              'Regime at entry',
            ].map((h, i) => (
              <th
                key={i}
                scope="col"
                style={{
                  padding: 'var(--sa-space-2) 10px',
                  textAlign: i === 0 || i === 1 || i === 8 || i === 9 ? 'left' : 'right',
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
              <td style={{ padding: '10px', ...mono, fontSize: 'var(--sa-text-md)' }}>
                {dayLabel(t.opened_at)} → {dayLabel(t.closed_at)}
              </td>
              <td style={{ padding: '10px', fontWeight: 600 }}>{t.symbol}</td>
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
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(t.adjusted) }}>
                {signedMoney(t.adjusted)}
              </td>
              <td style={{ padding: '10px', textAlign: 'right', ...mono, color: signColor(t.r) }}>
                {signedR(t.r)}
              </td>
              <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)' }}>{t.exit_label}</td>
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

export default function Performance() {
  const [runId, setRunId] = useState<number | undefined>(undefined);
  const [currency, setCurrency] = useState<'USD' | 'EUR'>('USD');

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

  const winRate = summary?.win_rate_pct;
  const dividends = Number(summary?.dividends_usd ?? 0);

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
                  columns={['', 'Trades', 'Won', 'P&L', 'Avg R'].map((label, j) => ({
                    label,
                    align: j === 0 ? 'left' : 'right',
                  }))}
                  rows={5}
                  track="1.4fr 0.7fr 0.6fr 0.8fr 0.7fr"
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
          <section aria-label="Summary" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}>
            <CardBoundary name="Performance summary">
            <StatCard
              label="Return"
              value={signedPct(summary.return_pct)}
              valueColor={signColor(summary.return_pct)}
              sub={`${signedMoney(summary.return_usd)}${
                summary.return_eur !== null
                  ? ` · ${signedMoney(summary.return_eur, '€')}`
                  : ''
              }`}
            />
            <StatCard
              label={
                <StatTip title="Agent return minus the return of buying SPY at the run's start and holding it.">
                  vs SPY hold
                </StatTip>
              }
              value={signedPct(summary.vs_spy_pct)}
              valueColor={signColor(summary.vs_spy_pct)}
              sub={
                summary.spy_return_pct !== null
                  ? `SPY ${signedPct(summary.spy_return_pct)} over the same days`
                  : 'no SPY baseline yet'
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
              sub={`${summary.slippage_pct}% slippage per side · ${
                dividends === 0
                  ? 'no dividends yet'
                  : `${signedMoney(summary.dividends_usd)} dividends`
              }`}
            />
            <StatCard
              label="Win rate"
              value={winRate === null ? '—' : `${Math.round(Number(winRate))}%`}
              sub={`${summary.wins} of ${summary.closed} closed trades`}
            />
            <StatCard
              label="Average R"
              value={signedR(summary.avg_r)}
              sub="per closed trade"
            />
            <StatCard
              label="Max drawdown"
              value={`−${Math.abs(Number(summary.max_drawdown_pct)).toFixed(2)}%`}
              valueColor={Number(summary.max_drawdown_pct) > 0 ? negative : 'var(--sa-text)'}
              sub={`close to close · kill at −${summary.kill_pct}%`}
            />
            </CardBoundary>
          </section>

          {curve && chartPoints.length > 0 && (
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
              <EquityCurveChart points={chartPoints} currency={chartCurrency} />
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
          )}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)' }}>
            <div style={{ flex: '1 1 440px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              <Card label="By regime" refreshing={res.refreshing}>
                <AggTable rows={data.by_regime ?? []} stale={res.refreshing} />
                <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
                  Down + high vol blocks new entries, so it never has trades.
                  {summary.closed < 30 &&
                    ` ${summary.closed} closed ${summary.closed === 1 ? 'trade is' : 'trades are'} far too few to judge any regime.`}
                </div>
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

          <TailsCard res={tails} />

          <Card label="Decisions and outcomes" refreshing={res.refreshing}>
            <div className={res.refreshing ? 'sa-stale-rows' : undefined} style={{ overflowX: 'auto', minWidth: 0 }}>
              <table style={{ width: '100%', minWidth: '520px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                    {['Decision', 'Count', 'Price after 1 session', 'Read'].map((h, i) => (
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
                      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>{row.count}</td>
                      <td
                        style={{
                          padding: '10px',
                          textAlign: 'right',
                          ...mono,
                          color:
                            row.key === 'enter'
                              ? signColor(row.avg_1session_pct)
                              : muted,
                        }}
                      >
                        {row.avg_1session_pct === null
                          ? '—'
                          : `${signedPct(row.avg_1session_pct)} avg`}
                      </td>
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

          <Card label="Closed trades" right={closedRight} refreshing={res.refreshing}>
            <ClosedTradesTable trades={data.closed_trades ?? []} stale={res.refreshing} />
          </Card>
        </>
      )}
    </div>
  );
}
