import { useState } from 'react';

import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import {
  FlattenConfirmDialog,
  KillSwitchDialog,
  ResumeDialog,
} from '../components/KillSwitchDialogs';
import type { PositionRow } from '../types/api';

const samplePositions: PositionRow[] = [
  {
    id: 1,
    symbol: 'AAPL',
    qty: 10,
    avg_entry_price: '168.50',
    market_value: '1685.00',
    current_stop_price: '163.20',
    target_price: '178.90',
    origin: 'agent',
    pending_ack: false,
    deferred_exit: null,
    opened_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
    closed_at: null,
    realized_pnl_raw: null,
    exit_reason: null,
  },
  {
    id: 2,
    symbol: 'MSFT',
    qty: 5,
    avg_entry_price: '412.10',
    market_value: '2060.50',
    current_stop_price: '400.00',
    target_price: '430.00',
    origin: 'agent',
    pending_ack: false,
    deferred_exit: null,
    opened_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
    closed_at: null,
    realized_pnl_raw: null,
    exit_reason: null,
  },
];

type DemoDialog = 'menu' | 'flatten' | 'resume' | null;

/** Gallery of the execution states and dialogs (design/screens/States.dc.html). */
export default function States() {
  const [dialog, setDialog] = useState<DemoDialog>(null);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>States & dialogs</h2>

      <Card label="Banners">
        <Banner variant="negative">
          Kill switch is on — no new entries. Open positions stay protected at Alpaca.
        </Banner>
        <Banner variant="warning">
          Reconciliation mismatch — Alpaca shows something different. Position XYZ at Alpaca is not in the database. · 15:42
        </Banner>
        <Banner variant="negative">
          Unknown position XYZ (12 shares) found at Alpaca. A protective stop was placed. Acknowledge to resume trading.
        </Banner>
        <Banner variant="info">
          Position check failed 3 times in a row — last error: read timeout.
        </Banner>
      </Card>

      <Card label="Dialogs">
        <div style={{ display: 'flex', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
          <Button variant="danger" onClick={() => setDialog('menu')}>Kill switch</Button>
          <Button onClick={() => setDialog('flatten')}>Sell everything confirmation</Button>
          <Button onClick={() => setDialog('resume')}>Resume trading</Button>
        </div>
      </Card>

      <KillSwitchDialog
        open={dialog === 'menu'}
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
      <FlattenConfirmDialog
        open={dialog === 'flatten'}
        positions={samplePositions}
        dayTrades={1}
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
      <ResumeDialog
        open={dialog === 'resume'}
        since={new Date(Date.now() - 45 * 60 * 1000).toISOString()}
        nextCycle={new Date(Date.now() + 20 * 60 * 1000).toISOString()}
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
    </div>
  );
}
