import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { getOrders, getPositions } from '../api/state';
import type { OrdersResponse, PositionRow, PositionsResponse } from '../types/api';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { Table } from '../components/Table';
import { displayDateTime } from '../format';

const money = (v: string | null) =>
  v == null ? '—' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function positionStatus(p: PositionRow): ReactNode {
  const chips: ReactNode[] = [];
  if (p.deferred_exit) {
    chips.push(<Chip key="deferred" variant="warning">Exit deferred (day-trade rule)</Chip>);
  }
  if (p.pending_ack) {
    chips.push(<Chip key="ack" variant="negative">Needs acknowledgment</Chip>);
  }
  if (p.origin === 'adopted') {
    chips.push(<Chip key="origin" variant="info">adopted</Chip>);
  }
  if (chips.length === 0) return <Chip variant="neutral">agent</Chip>;
  return <span style={{ display: 'inline-flex', gap: 6, justifyContent: 'flex-end' }}>{chips}</span>;
}

export default function Positions() {
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [orders, setOrders] = useState<OrdersResponse | null>(null);

  const refresh = useCallback(() => {
    getPositions().then(setPositions).catch(console.error);
    getOrders().then(setOrders).catch(console.error);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 15_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const open = positions?.open ?? [];
  const closedToday = positions?.closed_today ?? [];
  const adoptedPnl = closedToday
    .filter((p) => p.origin === 'adopted')
    .reduce((sum, p) => sum + Number(p.realized_pnl_raw ?? 0), 0);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Positions & orders</h2>

      <Card label="Open positions">
        <Table
          headers={['Symbol', 'Shares', 'Entry', 'Stop', 'Target', 'Value', 'Status']}
          rows={open.map((p) => [
            p.symbol,
            String(p.qty),
            money(p.avg_entry_price),
            money(p.current_stop_price),
            money(p.target_price),
            money(p.market_value),
            positionStatus(p),
          ])}
        />
        {open.length === 0 && (
          <p style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>No open positions.</p>
        )}
      </Card>

      <Card label={`Closed today · day trades (5d): ${positions?.day_trades_5d ?? 0}`}>
        <Table
          headers={['Symbol', 'Shares', 'Entry', 'P&L', 'Exit reason', 'Closed at', 'Origin']}
          rows={closedToday.map((p) => [
            p.symbol,
            String(p.qty),
            money(p.avg_entry_price),
            money(p.realized_pnl_raw),
            p.exit_reason ?? '—',
            displayDateTime(p.closed_at),
            p.origin,
          ])}
        />
        {closedToday.length === 0 && (
          <p style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>Nothing closed today.</p>
        )}
        {adoptedPnl !== 0 && (
          <p style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            Adopted positions P&L today: {money(String(adoptedPnl))} (tracked separately from agent trades).
          </p>
        )}
      </Card>

      <Card label="Orders">
        <Table
          headers={['Symbol', 'Leg', 'Side', 'Type', 'Qty', 'Limit', 'Stop', 'Status', 'Created']}
          rows={(orders?.orders ?? []).map((o) => [
            o.symbol ?? '—',
            o.leg,
            o.side,
            o.type,
            String(o.qty),
            money(o.limit_price),
            money(o.stop_price),
            o.status,
            displayDateTime(o.created_at),
          ])}
        />
        {(orders?.orders ?? []).length === 0 && (
          <p style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>No orders.</p>
        )}
      </Card>
    </div>
  );
}
