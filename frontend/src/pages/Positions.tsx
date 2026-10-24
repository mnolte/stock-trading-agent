import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { useLiveUpdates } from '../api/liveContext';
import { getOrders, getPositions } from '../api/state';
import type {
  OrderRow,
  PositionLeg,
  PositionRow,
  PositionsResponse,
} from '../types/api';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { formatPrice } from '../components/watchlistFormat';
import { displayDateTime, displayTime, signed, signedUsd } from '../format';

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
  if (p.deferred_exit) {
    chips.push(<Chip key="deferred" variant="warning">exit deferred</Chip>);
  }
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
        {p.opened_today &&
          ' · agent exit waits until tomorrow (day-trade guard)'}
        {p.earnings_date && ` · next earnings ${p.earnings_date}`}
      </div>
      {(p.legs ?? []).map((leg) => (
        <LegRow key={leg.id} leg={leg} />
      ))}
    </div>
  );
}

function OrderHistoryRow({ o }: { o: OrderRow }) {
  const fill = o.fills[0];
  return (
    <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
      {[
        displayDateTime(o.created_at),
        o.symbol,
        o.side,
        o.leg,
        o.type,
        String(o.qty),
        (o.limit_price ?? o.stop_price) === null ? 'market' : formatPrice(o.limit_price ?? o.stop_price),
        formatPrice(o.filled_avg_price),
        fill?.slippage ? formatPrice(fill.slippage) : '—',
        o.status,
      ].map((cell, i) => (
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
    </tr>
  );
}

export default function Positions() {
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [orders, setOrders] = useState<OrderRow[]>([]);

  const refresh = useCallback(() => {
    getPositions().then(setPositions).catch(console.error);
    getOrders().then((o) => setOrders(o.orders)).catch(console.error);
  }, []);

  useEffect(refresh, [refresh]);
  useLiveUpdates(['positions', 'orders'], refresh);

  const open = positions?.open ?? [];
  const closedToday = positions?.closed_today ?? [];
  const dayTrades = positions?.day_trades;
  const account = positions?.account;
  const recon = positions?.reconciliation;
  const recentOrders = orders.slice(0, 8);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Positions & orders</h2>

      <div
        style={{
          fontSize: 'var(--sa-text-md)',
          color: recon && !recon.ok ? 'var(--sa-negative-soft)' : 'var(--sa-text-muted)',
        }}
      >
        {recon === undefined || recon.ok
          ? 'In sync with Alpaca'
          : 'Reconciliation mismatch — Alpaca numbers are used'}
        {recon?.account_number && ` · account ${shortId(recon.account_number)}`} · paper
        {recon?.checked_at && ` · checked ${displayTime(recon.checked_at)}`}
      </div>

      <Card label={`Open positions · ${open.length} of ${positions?.max_open_positions ?? '—'}`}>
        {open.length === 0 && (
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            No open positions.
          </div>
        )}
        {open.map((p) => (
          <PositionCard key={p.id} p={p} />
        ))}
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
          <Card label="Day trades" right={`${dayTrades?.count ?? 0} of ${dayTrades?.max ?? 3}`}>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              in the last 5 trading days
            </div>
            <div style={{ display: 'flex', gap: 'var(--sa-space-2)' }}>
              {(dayTrades?.days ?? []).map((day) => (
                <div
                  key={day.date}
                  title={day.date}
                  style={{
                    width: '44px',
                    height: '44px',
                    borderRadius: 'var(--sa-radius-sm)',
                    border: '1px solid var(--sa-border)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    ...mono,
                    color: day.count > 0 ? 'var(--sa-warning-text)' : 'var(--sa-text-muted)',
                    background: day.count > 0 ? 'var(--sa-warning-bg)' : 'transparent',
                  }}
                >
                  {day.count}
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column' }}>
          <Card label="Account at Alpaca">
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

      <Card label="Order history" right={`last ${recentOrders.length} orders · times in Amsterdam`}>
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
        {recentOrders.length === 0 && (
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>No orders yet.</div>
        )}
      </Card>
    </div>
  );
}
