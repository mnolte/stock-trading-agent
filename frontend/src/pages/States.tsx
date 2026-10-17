import { useState, type ReactNode } from 'react';

import { ApprovalCard } from '../components/ApprovalCard';
import { Banner, type BannerVariant } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { sessionLabel, sessionTail } from '../components/sessionText';
import {
  FlattenConfirmDialog,
  KillSwitchDialog,
  ResumeDialog,
} from '../components/KillSwitchDialogs';
import { ResetAccountDialog, SaveSettingsDialog } from '../components/SettingsDialogs';
import { MarketClosed } from './Dashboard';
import type { Approval, MarketClosedCard, PositionRow, SessionState } from '../types/api';

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

const sessionSamples: { label: string; state: string; session: SessionState }[] = [
  {
    label: 'Open',
    state: 'open',
    session: {
      state: 'open',
      display_time: '',
      now: new Date(Date.now() - 6 * 60_000).toISOString(),
      next_cycle: new Date(Date.now() + 54 * 60_000).toISOString(),
    } as SessionState,
  },
  {
    label: 'Last cycle done',
    state: 'last_cycle_done',
    session: {
      state: 'last_cycle_done',
      display_time: '',
      now: new Date().toISOString(),
      next_cycle: null,
      next_close: new Date(Date.now() + 74 * 60_000).toISOString(),
    } as SessionState,
  },
  {
    label: 'Closed',
    state: 'closed',
    session: {
      state: 'closed',
      display_time: '',
      now: new Date().toISOString(),
      next_cycle: null,
      next_open: new Date(Date.now() + 16.5 * 3600_000).toISOString(),
    } as SessionState,
  },
  {
    label: 'Paused · after startup or Pause',
    state: 'paused',
    session: {
      state: 'paused',
      display_time: '',
      now: new Date().toISOString(),
      next_cycle: null,
    } as SessionState,
  },
  {
    label: 'Early close · e.g. 27 Nov, the day after Thanksgiving',
    state: 'early_close',
    session: {
      state: 'early_close',
      display_time: '',
      now: new Date().toISOString(),
      next_cycle: new Date(Date.now() + 40 * 60_000).toISOString(),
      next_close: new Date(Date.now() + 3 * 3600_000).toISOString(),
      cycles_today: 3,
    } as SessionState,
  },
];

const sampleMarketClosed: MarketClosedCard = {
  next_open: new Date(Date.now() + 16.5 * 3600_000).toISOString(),
  first_cycle: new Date(Date.now() + 17.75 * 3600_000).toISOString(),
  stops: [
    { symbol: 'MSFT', stop: '409.60' },
    { symbol: 'JPM', stop: '231.60' },
  ],
  open_count: 2,
  today_summary: '1 entry, 1 stop raised',
};

const sampleApproval: Approval = {
  id: 0,
  decision_id: 0,
  symbol: 'MSFT',
  qty: 4,
  limit_price: '418.40',
  stop_price: '409.60',
  target_price: '435.40',
  status: 'pending',
  reason: null,
  created_at: new Date(Date.now() - 12 * 60_000).toISOString(),
  resolved_at: null,
  confidence: 0.64,
  action: 'enter',
  thesis: null,
};

type DemoDialog = 'menu' | 'flatten' | 'resume' | 'reset' | 'save' | null;

function DemoBanner({
  label,
  sub,
  variant,
  children,
  actions,
}: {
  label: string;
  sub?: string;
  variant: BannerVariant;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 'var(--sa-text-xs)',
          fontWeight: 600,
          color: 'var(--sa-warning)',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          marginBottom: '4px',
        }}
      >
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', marginBottom: '4px' }}>{sub}</div>
      )}
      <Banner variant={variant} actions={actions}>
        {children}
      </Banner>
    </div>
  );
}

const bannerButtonStyle = { height: '36px', padding: '0 12px', fontSize: 'var(--sa-text-sm)' } as const;

/** Gallery of the execution states and dialogs (design/screens/States.dc.html). */
export default function States() {
  const [dialog, setDialog] = useState<DemoDialog>(null);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>States & dialogs</h2>

      <Card label="Session states">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          {sessionSamples.map((sample) => (
            <div key={sample.label}>
              <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginBottom: '4px' }}>{sample.label}</div>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 'var(--sa-space-3)',
                  height: '32px',
                  padding: '0 12px',
                  borderRadius: '999px',
                  background: 'var(--sa-raised)',
                  border: '1px solid var(--sa-border)',
                  fontSize: 'var(--sa-text-md)',
                }}
              >
                {sessionLabel(sample.state)}
                <span style={{ color: 'var(--sa-text-muted)' }}>
                  {sessionTail(sample.state, sample.session)}
                </span>
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card label="Banners">
        <DemoBanner
          label="Paused at startup"
          sub="Shown after every start until Resume"
          variant="info"
          actions={<Button style={bannerButtonStyle}>Resume</Button>}
        >
          <b>Started paused at 15:12.</b> Reconciled with Alpaca: 2 positions, both with a stop and target. Held positions are still reviewed each cycle; no new entries until you resume.
        </DemoBanner>
        <DemoBanner
          label="Kill switch on · manual"
          variant="negative"
          actions={
            <>
              <Button style={bannerButtonStyle}>Resume…</Button>
              <Button style={bannerButtonStyle}>Stop &amp; flatten…</Button>
            </>
          }
        >
          <b>Kill switch on since 19:02.</b> No new entries. Stops and targets at Alpaca stay in place. Reason: “testing the flow”.
        </DemoBanner>
        <DemoBanner
          label="Kill switch on · automatic"
          variant="negative"
          actions={<Button style={bannerButtonStyle}>Resume…</Button>}
        >
          <b>Kill switch turned on automatically at 20:46:</b> equity is 8.1% below its peak of $10,184.30. No new entries until you resume.
        </DemoBanner>
        <DemoBanner
          label="Position without a stop"
          sub="Critical; shown until acknowledged"
          variant="negative"
          actions={
            <>
              <Button style={bannerButtonStyle}>Open orders</Button>
              <Button style={bannerButtonStyle}>Acknowledge</Button>
            </>
          }
        >
          <b>JPM had no stop order at Alpaca.</b> A protective stop at 231.60 was placed at 18:46:04. Check the order history to see why the stop was missing.
        </DemoBanner>
        <DemoBanner
          label="Reconciliation mismatch"
          variant="warning"
          actions={
            <>
              <Button style={bannerButtonStyle}>Review</Button>
              <Button style={bannerButtonStyle}>Acknowledge</Button>
            </>
          }
        >
          <b>Alpaca shows 6 MSFT shares; the agent recorded 4.</b> Alpaca’s numbers are now used. New entries are blocked until you acknowledge.
        </DemoBanner>
        <DemoBanner
          label="Data source down"
          variant="warning"
          actions={<Button style={bannerButtonStyle}>Connections</Button>}
        >
          <b>Earnings calendar unavailable since 14:00.</b> New entries are blocked on every symbol until it is back. Open positions are unaffected.
        </DemoBanner>
        <DemoBanner label="Market data missing" variant="warning">
          <b>No new 5-minute bars since 18:15.</b> This cycle made no new entries. Stops at Alpaca still protect open positions.
        </DemoBanner>
        <DemoBanner label="Shock brake" variant="warning">
          <b>Shock brake on:</b> SPY fell 1.7% in the last hour. No new entries for the rest of this session.
        </DemoBanner>
        <DemoBanner label="Daily loss limit" variant="warning">
          <b>Daily loss limit reached:</b> −2.0% this session. No new entries until tomorrow’s first cycle.
        </DemoBanner>
        <DemoBanner label="Day-trade guard" variant="warning">
          <b>3 day trades in the last 5 trading days.</b> No new entries until the count drops on 20 Oct.
        </DemoBanner>
        <DemoBanner label="Losing streak" variant="info">
          <b>Risk per trade halved to 0.25%</b> after 3 losing trades in a row. Back to 0.5% after the next winning trade.
        </DemoBanner>
      </Card>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
        <span
          style={{
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 600,
            color: 'var(--sa-text-2)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--sa-label-tracking)',
          }}
        >
          Market closed · Dashboard
        </span>
        <MarketClosed mc={sampleMarketClosed} />
      </div>

      <Card label="Pending approval · supervised mode">
        <ApprovalCard
          approval={sampleApproval}
          killSwitchOn={false}
          onResolved={() => {}}
        />
      </Card>

      <Card label="Dialogs">
        <div style={{ display: 'flex', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
          <Button variant="danger" onClick={() => setDialog('menu')}>Kill switch</Button>
          <Button onClick={() => setDialog('flatten')}>Sell everything confirmation</Button>
          <Button onClick={() => setDialog('resume')}>Resume trading</Button>
          <Button onClick={() => setDialog('reset')}>Reset paper account</Button>
          <Button onClick={() => setDialog('save')}>Save settings</Button>
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
      <ResetAccountDialog
        open={dialog === 'reset'}
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
      <SaveSettingsDialog
        open={dialog === 'save'}
        version={4}
        diffs={[
          { label: 'Risk per trade', from: '0.5%', to: '0.4%' },
          { label: 'Max open positions', from: '4', to: '3' },
        ]}
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
    </div>
  );
}
