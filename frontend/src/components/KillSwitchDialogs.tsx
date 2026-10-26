import { useState } from 'react';

import type { PositionRow } from '../types/api';
import { displayTime, openedTodayNy } from '../format';
import { Banner } from './Banner';
import { DialogButton, Modal } from './Modal';

/** Kill switch dialog from design/screens/States.dc.html: pick what to stop. */
export function KillSwitchDialog({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: (scope: 'entries' | 'flatten') => void;
}) {
  const [scope, setScope] = useState<'entries' | 'flatten'>('entries');
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setScope('entries');
  }
  return (
    <Modal
      open={open}
      title="Kill switch"
      destructive
      onClose={onCancel}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>Cancel</DialogButton>
          <DialogButton variant="danger" onClick={() => onConfirm(scope)}>
            Turn on kill switch
          </DialogButton>
        </>
      }
    >
      <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
        Choose what to stop.
      </p>
      <RadioCard
        checked={scope === 'entries'}
        onSelect={() => setScope('entries')}
        title="Stop new entries"
        detail="Open positions keep their stops and targets at Alpaca."
      />
      <RadioCard
        checked={scope === 'flatten'}
        onSelect={() => setScope('flatten')}
        title="Stop & flatten"
        detail="Also cancel every order and sell every position at market."
      />
    </Modal>
  );
}

function RadioCard({
  checked,
  onSelect,
  title,
  detail,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  detail: string;
}) {
  return (
    <label
      style={{
        display: 'flex',
        gap: 'var(--sa-space-3)',
        padding: '14px',
        border: `1px solid ${checked ? 'var(--sa-accent)' : 'var(--sa-border)'}`,
        borderRadius: 'var(--sa-radius-md)',
        background: checked ? 'var(--sa-accent-bg)' : 'transparent',
        cursor: 'pointer',
      }}
    >
      <input type="radio" checked={checked} onChange={onSelect} style={{ marginTop: 3 }} />
      <span>
        <b style={{ fontWeight: 600 }}>{title}</b>
        <br />
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{detail}</span>
      </span>
    </label>
  );
}

/** Stop & flatten confirmation: positions table + day-trade warning. */
export function FlattenConfirmDialog({
  open,
  positions,
  dayTrades,
  dayTradesMax,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  positions: PositionRow[];
  dayTrades: number;
  dayTradesMax: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const todayPositions = positions.filter((p) => openedTodayNy(p.opened_at));
  return (
    <Modal
      open={open}
      title="Sell everything now?"
      destructive
      onClose={onCancel}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>Cancel</DialogButton>
          <DialogButton variant="danger" onClick={onConfirm}>Sell everything</DialogButton>
        </>
      }
    >
      <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
        This cancels all open orders and sells every position at market.
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 0 }}>
          <thead>
            <tr>
              {['Position', 'Shares', 'About'].map((h, i) => (
                <th
                  key={h}
                  scope="col"
                  style={{
                    textAlign: i === 0 ? 'left' : 'right',
                    padding: 'var(--sa-space-2) 10px',
                    fontSize: 'var(--sa-text-sm)',
                    fontWeight: 500,
                    color: 'var(--sa-text-muted)',
                    borderBottom: '1px solid var(--sa-border)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol}>
                <td style={{ textAlign: 'left', padding: '10px', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)' }}>
                  {p.symbol}
                </td>
                <td style={{ textAlign: 'right', padding: '10px', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', fontFamily: 'var(--sa-font-mono)' }}>
                  {p.qty}
                </td>
                <td style={{ textAlign: 'right', padding: '10px', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', fontFamily: 'var(--sa-font-mono)' }}>
                  {p.market_value
                    ? `$${Number(p.market_value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
                    : '—'}
                </td>
              </tr>
            ))}
            {positions.length === 0 && (
              <tr>
                <td colSpan={3} style={{ padding: '10px', color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
                  No open positions.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {todayPositions.map((p) => (
        <Banner key={p.symbol} variant="warning">
          {p.symbol} was bought today. Selling it now counts as a day trade (
          {dayTrades + 1} of {dayTradesMax}).
        </Banner>
      ))}
      <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-muted)' }}>
        Outside market hours the sells are queued as market-on-open orders.
      </p>
    </Modal>
  );
}

/** Resume dialog: a typed reason is required and stored in the log. */
export function ResumeDialog({
  open,
  since,
  nextCycle,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  since: string | null;
  nextCycle: string | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setReason('');
  }
  return (
    <Modal
      open={open}
      title="Resume trading?"
      onClose={onCancel}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>Cancel</DialogButton>
          <DialogButton variant="accent" disabled={!reason.trim()} onClick={() => onConfirm(reason)}>
            Resume
          </DialogButton>
        </>
      }
    >
      <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
        The kill switch has been on since {displayTime(since)}. New entries start
        again from the next cycle{nextCycle ? ` at ${displayTime(nextCycle)}` : ''}.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        <label htmlFor="resume-reason" style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
          Reason (required, stored in the log)
        </label>
        <textarea
          id="resume-reason"
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. checked the reconciliation, all positions match"
          style={{
            boxSizing: 'border-box',
            width: '100%',
            padding: '10px',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            background: 'var(--sa-bg)',
            color: 'var(--sa-text)',
            fontFamily: 'inherit',
            fontSize: 'var(--sa-text-base)',
            resize: 'vertical',
          }}
        />
      </div>
    </Modal>
  );
}
