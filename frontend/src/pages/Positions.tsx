import { useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { StopTargetBar } from '../components/StopTargetBar';
import { useLiveUpdates } from '../api/liveContext';
import { getOrders, getPositions } from '../api/state';
import { useApiResource, type Resource } from '../api/useResource';
import { useNewIds } from '../api/useNewIds';
import type {
  OrderRow,
  OrdersResponse,
  PositionLeg,
  PositionRow,
} from '../types/api';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { AlpacaStaleNote, CardBody } from '../components/cardState';
import { Chip } from '../components/Chip';
import { SkeletonList, SkeletonTable, SkeletonText } from '../components/skeletons';
import { formatPrice } from '../components/watchlistFormat';
import {
  displayDateTime,
  displayTime,
  openedTodayNy,
  signed,
  signedUsd, elapsedHm } from '../format';

const mono: React.CSSProperties = {
  fontFamily: 'var(--sa-font-mono)',
};

function fmtPnl(value: number | null | undefined): { text: string; color: string } {
  if (value == null) return { text: '—', color: 'var(--sa-text-muted)' };
  return {
    text: signedUsd(value),
    color: value >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)',
  };
}

function shortId(id: string | null): string {
  if (!id) return '—';
  return `${id.slice(0, 4)}…${id.slice(-4)}`;
}

function statusChips(p: PositionRow): ReactNode {
  const chips: ReactNode[] = [];
  if (p.pending_ack) {
    chips.push(<Chip key="ack" variant="negative">needs acknowledgment</Chip>);
  }
  if (p.origin === 'adopted') {
    chips.push(<Chip key="origin" variant="info">adopted</Chip>);
  }
  return chips.length > 0 ? <span style={{ display: 'inline-flex', gap: 6 }}>{chips}</span> : null;
}

function LegRow({ leg }: { leg: PositionLeg }) {
  const rawPrice = leg.limit_price ?? leg.stop_price;
  const price = rawPrice === null ? 'market' : formatPrice(rawPrice);
  const fill = leg.fills[0];
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '110px 110px 90px 90px 1fr',
        gap: 'var(--sa-space-3)',
        padding: '6px 0',
        fontSize: 'var(--sa-text-md)',
        alignItems: 'baseline',
      }}
    >
      <span style={{ color: 'var(--sa-text-muted)' }}>{leg.label}</span>
      <span style={{ ...mono, color: 'var(--sa-text-2)' }}>
        {leg.type} · gtc
      </span>
      <span style={{ ...mono, textAlign: 'right' }}>{price}</span>
      <span style={{ ...mono, color: 'var(--sa-text-muted)', textAlign: 'right' }}>
        {leg.status}
      </span>
      <span style={{ ...mono, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {shortId(leg.alpaca_order_id)}
        {fill && ` · ${formatPrice(fill.price)} at ${displayTime(fill.filled_at)}`}
        {leg.replaced_from && ` · replaced from ${leg.replaced_from}`}
      </span>
    </div>
  );
}

function Fact({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
      <span style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>{label}</span>
      <span style={{ ...mono, fontSize: 'var(--sa-text-base)', color: color ?? 'var(--sa-text)', whiteSpace: 'nowrap' }}>{value}</span>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{sub}</span>
    </div>
  );
}

function PositionCard({ p, near, fresh }: { p: PositionRow; near: number; fresh?: boolean }) {
  const location = useLocation();
  const pnl = fmtPnl(p.unrealized_pnl);
  const last = p.last_price != null ? Number(p.last_price) : null;
  const closeStop = p.to_stop_atr != null && p.to_stop_atr < near;
  const latest = p.latest_decision ?? null;
  const legs = p.legs ?? [];
  const pendingExit = p.pending_exit ?? null;
  const noStop = p.stop_missing != null;
  const legsProblem = p.legs_problem === true || pendingExit !== null || noStop;
  return (
    <div
      className={fresh ? 'sa-new' : undefined}
      style={{
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        padding: 'var(--sa-space-3) var(--sa-space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-2)',
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 'var(--sa-space-3)' }}>
        <span style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)' }}>{p.symbol}</span>
        <span style={{ ...mono, color: 'var(--sa-text-2)' }}>
          {p.qty} sh @ {formatPrice(p.avg_entry_price)}
        </span>
        {last !== null && (
          <span style={{ ...mono, color: 'var(--sa-text-muted)' }}>last {formatPrice(p.last_price ?? null)}</span>
        )}
        <span style={{ ...mono, color: pnl.color }}>
          {pnl.text}
          {p.unrealized_pnl_eur != null && (
            <span style={{ color: 'var(--sa-text-muted)' }}> · €{p.unrealized_pnl_eur.toFixed(2)}</span>
          )}
        </span>
        <span style={{ ...mono, color: 'var(--sa-text-muted)' }}>
          {p.locked_in ? 'stop above entry' : p.r_multiple != null ? `${signed(p.r_multiple)}R` : ''}
        </span>
        {statusChips(p)}
        {noStop && (
          <Chip variant="negative">
            No stop at Alpaca since {displayTime(p.stop_missing!.since)} ·{' '}
            {elapsedHm(p.stop_missing!.since)}
          </Chip>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {p.opened_today
            ? `opened today ${displayTime(p.opened_at)}`
            : `opened ${displayDateTime(p.opened_at)}`}
          {p.earnings_date && ` · next earnings ${p.earnings_date}`}
        </span>
      </div>
      {pendingExit && (
        <div
          role="status"
          style={{
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-warning-text)',
          }}
        >
          exit held back{pendingExit.since ? ` since ${displayTime(pendingExit.since)}` : ''}
          {pendingExit.tries ? ` · ${pendingExit.tries} tr${pendingExit.tries === 1 ? 'y' : 'ies'}` : ''}
          {pendingExit.failure ? ` · ${pendingExit.failure}` : ''}
        </div>
      )}
      <StopTargetBar
        stop={p.current_stop_price != null ? Number(p.current_stop_price) : null}
        entry={Number(p.avg_entry_price)}
        last={last}
        target={p.target_price != null ? Number(p.target_price) : null}
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 'var(--sa-space-3)' }}>
        <Fact
          label="If the stop hits"
          value={p.if_stop_usd != null ? signedUsd(p.if_stop_usd) : '—'}
          sub={`${p.if_stop_r != null ? `${signed(p.if_stop_r)}R` : '—'}${p.locked_in ? ' · locked in' : ''}`}
          color={p.locked_in ? 'var(--sa-positive)' : 'var(--sa-negative-text)'}
        />
        <Fact
          label="To the stop"
          value={p.to_stop_pct != null ? `${p.to_stop_pct.toFixed(2)}%` : '—'}
          sub={`${p.to_stop_atr != null ? `${p.to_stop_atr.toFixed(1)}` : '—'} ATR (1h)`}
          color={closeStop ? 'var(--sa-warning-text)' : 'var(--sa-text)'}
        />
        <Fact
          label="To the target"
          value={p.to_target_pct != null ? `${p.to_target_pct.toFixed(2)}%` : '—'}
          sub={`${p.to_target_atr != null ? `${p.to_target_atr.toFixed(1)}` : '—'} ATR${p.levels_to_target != null ? ` · ${p.levels_to_target} level${p.levels_to_target === 1 ? '' : 's'} in the way` : ''}`}
        />
        <Fact
          label="Exit planned"
          value={p.scheduled_exit ? p.scheduled_exit.replace('earnings exit ', '') : 'none'}
          sub={p.scheduled_exit ? 'earnings exit' : 'stop or target at Alpaca'}
        />
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
        {p.scheduled_exit && (
          <Chip variant="info">{p.scheduled_exit}</Chip>
        )}
        {p.stop_distance_label === 'stop above entry' && (
          <Chip variant="neutral">stop above entry</Chip>
        )}
        <span style={{ flex: 1 }} />
        {latest && (
          <Link
            to={`/decisions/${latest.id}`}
            state={{ backgroundLocation: location }}
            style={{ fontSize: 'var(--sa-text-md)' }}
          >
            Latest decision: {latest.action.toUpperCase()} {displayTime(latest.time)}
            {latest.confidence != null ? ` · ${latest.confidence.toFixed(2)}` : ''} →
          </Link>
        )}
      </div>
      <details
        open={legsProblem}
        style={{
          borderTop: '1px solid var(--sa-border)',
          paddingTop: 'var(--sa-space-2)',
          color: legsProblem ? 'var(--sa-warning-text)' : undefined,
        }}
      >
        <summary style={{ cursor: 'pointer', minHeight: 32, display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--sa-text-md)', color: legsProblem ? 'var(--sa-warning-text)' : 'var(--sa-text-2)' }}>
          Orders at Alpaca ·{' '}
          {noStop
            ? `no stop at Alpaca${p.stop_missing?.reason ? ` — ${p.stop_missing.reason}` : ''}`
            : pendingExit
              ? `exit pending: ${pendingExit.failure ?? pendingExit.reason ?? 'sell held back'}`
              : p.legs_summary ?? `${legs.length} legs`}
        </summary>
        {noStop && (
          <div style={{ ...mono, fontSize: 'var(--sa-text-md)', color: 'var(--sa-negative-text)', padding: '4px 0' }}>
            stop none
          </div>
        )}
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: '460px' }}>
            {legs.map((leg) => (
              <LegRow key={leg.id} leg={leg} />
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}

/** Order status as a small chip (design/screens/Positions.dc.html):
 * filled is positive, open is neutral, the rest muted. */
function OrderStatus({ status }: { status: string }) {
  const variant =
    status === 'filled'
      ? 'positive'
      : status === 'open' || status === 'accepted' || status === 'partially_filled'
        ? 'info'
        : 'neutral';
  return <Chip variant={variant}>{status.replace(/_/g, ' ')}</Chip>;
}

const OPEN_ORDER_STATUSES = new Set([
  'new', 'accepted', 'pending_new', 'accepted_for_bidding',
  'partially_filled', 'held', 'calculated', 'pending_cancel',
  'pending_replace', 'pending_review',
]);

/** The "Problems" filter (design/screens/Positions.dc.html): rejected,
 * ended without a fill, replaced after an error, or a stop that filled
 * worse than its stop price. */
function orderProblems(o: OrderRow): string[] {
  const problems: string[] = [];
  if (o.status === 'rejected') problems.push('rejected');
  if (!OPEN_ORDER_STATUSES.has(o.status) && o.status !== 'filled' && o.fills.length === 0) {
    problems.push('not filled');
  }
  if (o.replaced_from) problems.push('replaced after an error');
  if (
    o.leg === 'stop' &&
    o.filled_avg_price !== null &&
    o.stop_price !== null &&
    Number(o.filled_avg_price) < Number(o.stop_price)
  ) {
    problems.push('fill worse than the stop');
  }
  return problems;
}

/** NY date string of an ISO instant — the period filter's session day. */
function nyDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
}

/** The last n weekday session days ending today (display filter — the
 * trading calendar itself lives on the backend). */
function lastWeekdayDays(n: number): Set<string> {
  const days = new Set<string>();
  const d = new Date(
    new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) + 'T00:00:00',
  );
  while (days.size < n) {
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) days.add(nyDay(d.toISOString()));
    d.setDate(d.getDate() - 1);
  }
  return days;
}

type Period = 'today' | '5' | '20' | 'all';

function OrderHistoryRow({ o, problem, fresh }: { o: OrderRow; problem: string[]; fresh?: boolean }) {
  const fill = o.fills[0];
  const rejected = o.status === 'rejected';
  const cells = [
    displayDateTime(o.created_at),
    o.symbol,
    o.side,
    o.leg,
    o.type,
    String(o.qty),
    (o.limit_price ?? o.stop_price) === null ? 'market' : formatPrice(o.limit_price ?? o.stop_price),
    formatPrice(o.filled_avg_price),
    fill?.slippage !== null && fill?.slippage !== undefined ? signed(Number(fill.slippage)) : '—',
  ];
  return (
    <tr className={fresh ? 'sa-new' : undefined} style={{ borderBottom: '1px solid var(--sa-border)' }}>
      {cells.map((cell, i) => (
        <td
          key={i}
          style={{
            padding: 'var(--sa-space-2) 10px',
            textAlign: i === 0 ? 'left' : 'right',
            ...mono,
            fontSize: 'var(--sa-text-md)',
            color: rejected ? 'var(--sa-negative-text)' : 'var(--sa-text-2)',
            whiteSpace: 'nowrap',
          }}
        >
          {cell}
        </td>
      ))}
      <td
        style={{
          padding: 'var(--sa-space-2) 10px',
          textAlign: 'right',
          whiteSpace: 'nowrap',
        }}
      >
        <OrderStatus status={o.status} />
        {problem.length > 0 && o.status !== 'rejected' && (
          <div style={{ fontSize: 'var(--sa-text-xs, 11px)', color: 'var(--sa-warning-text)' }}>
            {problem.join(' · ')}
          </div>
        )}
      </td>
    </tr>
  );
}

function OrderHistory({ res }: { res: Resource<OrdersResponse> }) {
  const [filter, setFilter] = useState<'all' | 'problems'>('all');
  const [period, setPeriod] = useState<Period>('today');
  const [shown, setShown] = useState(20);
  const orders = res.data?.orders ?? [];
  const freshOrders = useNewIds(orders.map((o) => o.id));
  const periodDays =
    period === 'today'
      ? lastWeekdayDays(1)
      : period === '5'
        ? lastWeekdayDays(5)
        : period === '20'
          ? lastWeekdayDays(20)
          : null;
  const filtered = orders.filter(
    (o) =>
      (periodDays === null || periodDays.has(nyDay(o.created_at))) &&
      (filter === 'all' || orderProblems(o).length > 0),
  );
  const visible = filtered.slice(0, shown);
  const sel = (active: boolean): React.CSSProperties => ({
    height: 'var(--sa-control-height)',
    padding: '0 var(--sa-space-3)',
    borderRadius: 'var(--sa-radius-md)',
    border: `1px solid ${active ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
    background: active ? 'var(--sa-accent-bg)' : 'var(--sa-raised)',
    color: active ? 'var(--sa-accent)' : 'var(--sa-text-2)',
    fontSize: 'var(--sa-text-sm)',
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
  });
  return (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
        {(['all', 'problems'] as const).map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => { setFilter(f); setShown(20); }} style={sel(filter === f)}>
            {f === 'all' ? 'All' : 'Problems'}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        {(['today', '5', '20', 'all'] as const).map((p) => (
          <button key={p} type="button" aria-pressed={period === p} onClick={() => { setPeriod(p); setShown(20); }} style={sel(period === p)}>
            {p === 'all' ? 'All' : p === 'today' ? 'Today' : `${p} sessions`}
          </button>
        ))}
      </div>
      <CardBody
        res={res}
        what="the order history"
        skeleton={
          <div style={{ overflowX: 'auto' }}>
            <div style={{ minWidth: '860px' }}>
              <SkeletonTable
                columns={['Time', 'Symbol', 'Side', 'Leg', 'Type', 'Qty', 'Price', 'Fill', 'Slippage', 'Status'].map(
                  (label, i) => ({ label, align: i === 0 ? 'left' : 'right' }),
                )}
                rows={8}
                track="repeat(10, minmax(64px, 1fr))"
              />
            </div>
          </div>
        }
        isEmpty={() => visible.length === 0}
        empty={
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            {filter === 'problems' ? 'No order problems in this period.' : 'No orders in this period.'}
          </div>
        }
      >
        {() => (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: '860px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                  {['Time', 'Symbol', 'Side', 'Leg', 'Type', 'Qty', 'Price', 'Fill', 'Slippage', 'Status'].map((h, i) => (
                    <th
                      key={h}
                      style={{
                        padding: 'var(--sa-space-2) 10px',
                        textAlign: i === 0 ? 'left' : 'right',
                        fontSize: 'var(--sa-text-sm)',
                        color: 'var(--sa-text-muted)',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((o) => (
                  <OrderHistoryRow key={o.id} o={o} problem={orderProblems(o)} fresh={freshOrders.has(o.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
      {filtered.length > shown && (
        <button
          type="button"
          onClick={() => setShown((s) => s + 20)}
          style={{
            height: 'var(--sa-control-height)',
            alignSelf: 'flex-start',
            padding: '0 var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-sm)',
            border: '1px solid var(--sa-border)',
            background: 'var(--sa-raised)',
            color: 'var(--sa-text-2)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          Load 20 more ({filtered.length - shown} left)
        </button>
      )}
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
        Slippage is the fill minus the intended price per share — negative is
        better for a buy. A stop that gaps at the open fills at the open
        price; that difference shows here too.
      </div>
    </>
  );
}

export default function Positions() {
  const positionsRes = useApiResource((signal) => getPositions(signal));
  const ordersRes = useApiResource((signal) => getOrders(signal));

  const reloadAll = () => {
    positionsRes.reload();
    ordersRes.reload();
  };
  useLiveUpdates(['positions', 'orders'], reloadAll);

  const positions = positionsRes.data;
  const open = positions?.open ?? [];
  const freshPositions = useNewIds(open.map((p) => p.id));
  const closedToday = positions?.closed_today ?? [];
  const recon = positions?.reconciliation;
  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-2)',
          fontSize: 'var(--sa-text-md)',
          color: recon && !recon.ok ? 'var(--sa-negative-soft)' : 'var(--sa-text-muted)',
        }}
      >
        {recon === undefined || recon.ok ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sa-space-2)', whiteSpace: 'nowrap' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--sa-positive)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="4 12 9 17 20 6" />
            </svg>
            <span style={{ color: 'var(--sa-positive)' }}>In sync with Alpaca</span>
          </span>
        ) : (
          'Reconciliation mismatch — Alpaca numbers are used'
        )}
        {recon?.account_number && ` · account ${shortId(recon.account_number)}`} · paper
        {recon?.checked_at && ` · checked ${displayTime(recon.checked_at)}`}
      </div>

      {positions?.totals && open.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px', padding: '12px 16px', background: 'var(--sa-panel)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-lg)', fontSize: 'var(--sa-text-md)' }}>
          <CardBoundary name="Position totals">
          {positions?.stale && <AlpacaStaleNote asOf={positions.as_of ?? null} />}
          <span><span style={{ color: 'var(--sa-text-muted)' }}>Unrealized</span>{' '}
            <span style={{ ...mono, color: (positions.totals.unrealized_usd ?? 0) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>{positions.totals.unrealized_usd != null ? signedUsd(positions.totals.unrealized_usd) : '—'}</span></span>
          <span><span style={{ color: 'var(--sa-text-muted)' }}>If every stop hits</span>{' '}
            <span style={{ ...mono, color: 'var(--sa-negative-text)' }}>{positions.totals.if_stops_usd != null ? signedUsd(positions.totals.if_stops_usd) : '—'}</span>{' '}
            <span style={{ color: 'var(--sa-text-muted)' }}>· {positions.totals.if_stops_pct_equity != null ? `${Math.abs(positions.totals.if_stops_pct_equity).toFixed(2)}%` : '—'} of equity</span></span>
          <span><span style={{ color: 'var(--sa-text-muted)' }}>Open risk</span>{' '}
            <span style={mono}>{positions.totals.open_risk_pct != null ? `${positions.totals.open_risk_pct.toFixed(2)}%` : '—'} of {positions.totals.open_risk_cap_pct}%</span></span>
          {positions.totals.unprotected > 0 && (
            <span><span style={{ ...mono, color: 'var(--sa-negative-text)' }}>
              {positions.totals.unprotected} without a stop
            </span>{' '}
            <span style={{ color: 'var(--sa-text-muted)' }}>· counted at full value in open risk</span></span>
          )}
          {positions.totals.closest && (
            <span><span style={{ color: 'var(--sa-text-muted)' }}>Closest to its stop</span>{' '}
              <span style={{ ...mono, color: positions.totals.closest.atr < Number(positions.near_stop_atr ?? 1.5) ? 'var(--sa-warning-text)' : 'var(--sa-text)' }}>
                {positions.totals.closest.symbol} {positions.totals.closest.atr.toFixed(1)} ATR
              </span>{' '}
              <span style={{ color: 'var(--sa-text-muted)' }}>· amber under {positions.near_stop_atr ?? '1.5'} ATR</span></span>
          )}
          </CardBoundary>
        </div>
      )}

      <Card
        label={`Open positions · ${open.length} of ${positions?.max_open_positions ?? '—'}`}
        busy={positionsRes.loading}
        refreshing={positionsRes.refreshing}
      >
        {positions?.stale && <AlpacaStaleNote asOf={positions.as_of ?? null} />}
        <CardBody
          res={positionsRes}
          what="positions"
          skeleton={<SkeletonList rows={3} />}
          isEmpty={(d) => d.open.length === 0}
          empty={
            <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
              No open positions.
            </div>
          }
        >
          {() => <>{open.map((p) => <PositionCard key={p.id} p={p} near={Number(positions?.near_stop_atr ?? 1.5)} fresh={freshPositions.has(p.id)} />)}</>}
        </CardBody>
      </Card>

      {closedToday.length > 0 && (
        <Card label="Closed today">
          {closedToday.map((p) => (
            <div key={p.id} style={{ display: 'flex', gap: 'var(--sa-space-3)', fontSize: 'var(--sa-text-md)' }}>
              <b>{p.symbol}</b>
              <span style={mono}>{p.qty} sh</span>
              <span style={{ ...mono, color: fmtPnl(p.realized_pnl_raw).color }}>
                {fmtPnl(p.realized_pnl_raw).text}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>{p.exit_reason ?? ''}</span>
            </div>
          ))}
        </Card>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)', alignItems: 'stretch' }}>
        <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column' }}>
          <Card label="Today" busy={positionsRes.loading} refreshing={positionsRes.refreshing} style={{ flex: 1 }}>
            <CardBody
              res={positionsRes}
              what="today's counts"
              skeleton={<SkeletonText lines={4} />}
            >
              {(d) => {
                const t = d.today;
                const entries = [
                  ...d.open.filter((p) => p.opened_today).map((p) => p.symbol),
                  ...d.closed_today
                    .filter((p) => openedTodayNy(p.opened_at))
                    .map((p) => p.symbol),
                ];
                const exits = d.closed_today.map((p) => p.symbol);
                return (
            <>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) auto',
                gap: 'var(--sa-space-2) var(--sa-space-4)',
                fontSize: 'var(--sa-text-md)',
              }}
            >
              <span style={{ color: 'var(--sa-text-muted)' }}>Entries</span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {t
                  ? `${t.entries}${entries.length ? ` · ${entries.join(', ')}` : ''}`
                  : '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Exits</span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {t
                  ? `${t.exits}${exits.length ? ` · ${exits.join(', ')}` : ''}`
                  : '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                Bought and sold the same session
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {t?.round_trips ?? '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                Not to be bought again today
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {t ? t.reentry_blocked.join(', ') || '—' : '—'}
              </span>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
              There is no day-trade limit since 4 June 2026 (FINRA replaced the
              pattern day trader rule). The agent may sell a position the day it
              bought it. A symbol it closed today isn't bought again until the
              next session.
            </div>
            </>
                );
              }}
            </CardBody>
          </Card>
        </div>
        <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column' }}>
          <Card label="Account at Alpaca" busy={positionsRes.loading} refreshing={positionsRes.refreshing} style={{ flex: 1 }}>
            {positions?.stale && <AlpacaStaleNote asOf={positions.as_of ?? null} />}
            <CardBody
              res={positionsRes}
              what="the account"
              skeleton={<SkeletonText lines={5} />}
            >
              {(d) => {
                const a = d.account;
                return (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: '6px var(--sa-space-4)', fontSize: 'var(--sa-text-md)' }}>
              <span style={{ color: 'var(--sa-text-muted)' }}>Equity</span>
              <span style={{ ...mono, textAlign: 'right' }}>
                ${a?.equity ?? '—'}
                {a?.equity_eur && <span style={{ color: 'var(--sa-text-muted)' }}> · €{a.equity_eur}</span>}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Cash</span>
              <span style={{ ...mono, textAlign: 'right' }}>${a?.cash ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Positions value</span>
              <span style={{ ...mono, textAlign: 'right' }}>${a?.positions_value ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Margin used</span>
              <span style={{ ...mono, textAlign: 'right' }}>{a?.margin ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                EUR/USD{a?.eur_usd_date ? ` (ECB, ${a.eur_usd_date})` : ''}
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>{a?.eur_usd_rate ?? '—'}</span>
            </div>
                );
              }}
            </CardBody>
          </Card>
        </div>
      </div>

      <Card label="Order history" right="times in Amsterdam" busy={ordersRes.loading} refreshing={ordersRes.refreshing}>
        <OrderHistory res={ordersRes} />
      </Card>
    </div>
  );
}
