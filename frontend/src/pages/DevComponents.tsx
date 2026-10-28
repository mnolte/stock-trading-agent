import { useState } from 'react';

import { ActionBadge } from '../components/ActionBadge';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { CheckRow } from '../components/CheckRow';
import { Chip } from '../components/Chip';
import { Dialog } from '../components/Dialog';
import { NumberField } from '../components/NumberField';
import { SegmentedControl } from '../components/SegmentedControl';
import { StatCard } from '../components/StatCard';
import { Table } from '../components/Table';
import { Toggle } from '../components/Toggle';

export default function DevComponents() {
  const [dialogOpen, setDialogOpen] = useState(false);
  // Test hook: ?crash throws on render so the screen boundary can be
  // exercised (Playwright).
  if (new URLSearchParams(window.location.search).has('crash')) {
    throw new Error('e2e render crash');
  }

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Shared components</h2>

      <Card label="Stat cards" right={<a href="#">Link</a>}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}>
          <StatCard label="Open risk" value="0.34%" bar={{ width: '14%', color: 'var(--sa-accent)' }} sub="of 2.5% cap · $34.40" />
          <StatCard label="Positions" value="2 of 4" sub="cash $6,634.30 · no margin" />
        </div>
      </Card>

      <Card label="Chips">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          <Chip variant="neutral">neutral</Chip>
          <Chip variant="positive">positive</Chip>
          <Chip variant="negative">negative</Chip>
          <Chip variant="warning">warning</Chip>
          <Chip variant="info">info</Chip>
          <Chip variant="accent">accent</Chip>
        </div>
      </Card>

      <Card label="Action badges">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'center' }}>
          <ActionBadge action="ENTER" />
          <ActionBadge action="SKIP" />
          <ActionBadge action="HOLD" />
          <ActionBadge action="TIGHTEN STOP" />
          <ActionBadge action="EXIT" size="large" />
          <ActionBadge action="NO CALL" size="large" />
        </div>
      </Card>

      <Card label="Check rows">
        <div style={{ border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', padding: '4px 12px' }}>
          <CheckRow label="EMA 20 above EMA 50" variant="pass" />
          <CheckRow label="Spread below 0.15%" variant="fail" />
          <CheckRow label="Size reduced to fit" variant="adjusted" />
        </div>
      </Card>

      <Card label="Banners">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          <Banner variant="negative" actions={<Button variant="ghost">Acknowledge</Button>}>
            Reconciliation mismatch: Alpaca state differs from the local database.
          </Banner>
          <Banner variant="warning">Paper account balance is below expected equity.</Banner>
          <Banner variant="info">Next cycle runs at 10:46 New York time.</Banner>
        </div>
      </Card>

      <Card label="Buttons">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'center' }}>
          <Button variant="default">Default</Button>
          <Button variant="primary">Primary</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
        </div>
      </Card>

      <Card label="Controls">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)', alignItems: 'center' }}>
          <SegmentedControl options={['Automatic', 'Supervised']} />
          <Toggle label="Hold through earnings" />
        </div>
      </Card>

      <Card label="Number field">
        <NumberField label="Risk per trade" unit="%" help="Percent of equity risked on each trade" defaultValue={0.5} />
      </Card>

      <Card label="Table">
        <Table
          headers={['Symbol', 'Price', 'Day', 'Earnings']}
          rows={[
            ['MSFT', '431.50', '+1.2%', '23 Oct'],
            ['AAPL', '225.30', '-0.4%', '30 Oct'],
          ]}
        />
      </Card>

      <Card label="Dialog">
        <Button variant="danger" onClick={() => setDialogOpen(true)}>
          Open dialog
        </Button>
        <Dialog open={dialogOpen} title="Stop & flatten" onConfirm={() => setDialogOpen(false)} onCancel={() => setDialogOpen(false)}>
          This will cancel all open orders and sell every position at market. Continue?
        </Dialog>
      </Card>
    </div>
  );
}
