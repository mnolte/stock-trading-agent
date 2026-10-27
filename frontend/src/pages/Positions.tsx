import type { ReactNode } from 'react';

import { useLiveUpdates } from '../api/liveContext';
import { getOrders, getPositions } from '../api/state';
import { useApiResource } from '../api/useResource';
import type {
  OrderRow,
  PositionLeg,
  PositionRow,
} from '../types/api';
import { Card } from '../components/Card';
import { CardBody } from '../components/cardState';
import { Chip } from '../components/Chip';
import { formatPrice } from '../components/watchlistFormat';
import {
  displayDateTime,
  displayTime,
  openedTodayNy,
  signed,
  signedUsd,
} from '../format';

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

function PositionCard({ p }: { p: PositionRow }) {
  const pnl = fmtPnl(p.unrealized_pnl);
  return (
    <div
      style={{
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        padding: 'var(--sa-space-3) var(--sa-space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-2)',
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 'var(--sa-space-3)' }}>
        <span style={{ fontWeight: 600 }}>{p.symbol}</span>
        <span style={{ ...mono, color: 'var(--sa-text-2)' }}>
          {p.qty} sh @ {formatPrice(p.avg_entry_price)}
        </span>
        {p.last_price && (
          <span style={{ ...mono, color: 'var(--sa-text-muted)' }}>last {formatPrice(p.last_price)}</span>
        )}
        <span style={{ ...mono, color: pnl.color }}>
          {pnl.text}
          {p.unrealized_pnl_eur != null && (
            <span style={{ color: 'var(--sa-text-muted)' }}> · €{p.unrealized_pnl_eur.toFixed(2)}</span>
          )}
        </span>
        {p.r_multiple != null && (
          <span style={{ ...mono, color: 'var(--sa-text-muted)' }}>
            {signed(p.r_multiple)}R
          </span>
        )}
        {p.stop_distance_label && (
          <Chip variant="neutral">{p.stop_distance_label}</Chip>
        )}
        {statusChips(p)}
      </div>
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {p.opened_today
          ? `opened today ${displayTime(p.opened_at)}`
          : `opened ${displayDateTime(p.opened_at)}`}
        {p.earnings_date && ` · next earnings ${p.earnings_date}`}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ minWidth: '460px' }}>
          {(p.legs ?? []).map((leg) => (
            <LegRow key={leg.id} leg={leg} />
          ))}
        </div>
      </div>
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

function OrderHistoryRow({ o }: { o: OrderRow }) {
  const fill = o.fills[0];
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
    <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
      {cells.map((cell, i) => (
        <td
          key={i}
          style={{
            padding: 'var(--sa-space-2) 10px',
            textAlign: i === 0 ? 'left' : 'right',
            ...mono,
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
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
      </td>
    </tr>
  );
}

export default function Positions() {
  const positionsRes = useApiResource(getPositions);
  const ordersRes = useApiResource(getOrders);

  const reloadAll = () => {
    positionsRes.reload();
    ordersRes.reload();
  };
  useLiveUpdates(['positions', 'orders'], reloadAll);

  const positions = positionsRes.data;
  const open = positions?.open ?? [];
  const closedToday = positions?.closed_today ?? [];
  const today = positions?.today;
  // Symbols behind the counts: entries are positions opened this session,
  // open or already closed (design/screens/Positions.dc.html, "Today").
  const entrySymbols = [
    ...open.filter((p) => p.opened_today).map((p) => p.symbol),
    ...closedToday
      .filter((p) => openedTodayNy(p.opened_at))
      .map((p) => p.symbol),
  ];
  const exitSymbols = closedToday.map((p) => p.symbol);
  const account = positions?.account;
  const recon = positions?.reconciliation;
  const recentOrders = ordersRes.data?.orders.slice(0, 8) ?? [];

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

      <Card label={`Open positions · ${open.length} of ${positions?.max_open_positions ?? '—'}`} busy={positionsRes.loading}>
        <CardBody
          res={positionsRes}
          what="positions"
          isEmpty={(d) => d.open.length === 0}
          empty={
            <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
              No open positions.
            </div>
          }
        >
          {() => <>{open.map((p) => <PositionCard key={p.id} p={p} />)}</>}
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
          <Card label="Today" busy={positionsRes.loading} style={{ flex: 1 }}>
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
                {today
                  ? `${today.entries}${entrySymbols.length ? ` · ${entrySymbols.join(', ')}` : ''}`
                  : '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Exits</span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {today
                  ? `${today.exits}${exitSymbols.length ? ` · ${exitSymbols.join(', ')}` : ''}`
                  : '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                Bought and sold the same session
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {today?.round_trips ?? '—'}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                Not to be bought again today
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>
                {today ? today.reentry_blocked.join(', ') || '—' : '—'}
              </span>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
              There is no day-trade limit since 4 June 2026 (FINRA replaced the
              pattern day trader rule). The agent may sell a position the day it
              bought it. A symbol it closed today isn't bought again until the
              next session.
            </div>
          </Card>
        </div>
        <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column' }}>
          <Card label="Account at Alpaca" busy={positionsRes.loading} style={{ flex: 1 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: '6px var(--sa-space-4)', fontSize: 'var(--sa-text-md)' }}>
              <span style={{ color: 'var(--sa-text-muted)' }}>Equity</span>
              <span style={{ ...mono, textAlign: 'right' }}>
                ${account?.equity ?? '—'}
                {account?.equity_eur && <span style={{ color: 'var(--sa-text-muted)' }}> · €{account.equity_eur}</span>}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Cash</span>
              <span style={{ ...mono, textAlign: 'right' }}>${account?.cash ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Positions value</span>
              <span style={{ ...mono, textAlign: 'right' }}>${account?.positions_value ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Margin used</span>
              <span style={{ ...mono, textAlign: 'right' }}>{account?.margin ?? '—'}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>
                EUR/USD{account?.eur_usd_date ? ` (ECB, ${account.eur_usd_date})` : ''}
              </span>
              <span style={{ ...mono, textAlign: 'right' }}>{account?.eur_usd_rate ?? '—'}</span>
            </div>
          </Card>
        </div>
      </div>

      <Card label="Order history" right={`last ${recentOrders.length} orders · times in Amsterdam`} busy={ordersRes.loading}>
        <CardBody
          res={ordersRes}
          what="the order history"
          isEmpty={() => recentOrders.length === 0}
          empty={
            <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>No orders yet.</div>
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
                  {recentOrders.map((o) => (
                    <OrderHistoryRow key={o.id} o={o} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
          Slippage is the fill minus the intended price per share — negative is
          better for a buy. A stop that gaps at the open fills at the open
          price; that difference shows here too.
        </div>
      </Card>
    </div>
  );
}
