import { useEffect, useState } from 'react';

import { DialogButton, Modal } from './Modal';

/** "Reset the paper account? Type RESET to confirm" (design: States). */
export function ResetAccountDialog({
  open,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (open) setTyped('');
  }, [open]);
  return (
    <Modal
      open={open}
      title="Reset the paper account?"
      destructive
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>Cancel</DialogButton>
          <DialogButton variant="danger" onClick={onConfirm} disabled={typed !== 'RESET' || busy}>
            Reset account
          </DialogButton>
        </>
      }
    >
      <p style={{ margin: 0, color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
        This turns the kill switch on, closes all positions and cancels all orders at Alpaca.
      </p>
      <input
        aria-label="Type RESET to confirm"
        placeholder="Type RESET to confirm"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        style={{ height: 'var(--sa-input-height)', padding: '0 12px', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-text)', fontFamily: 'var(--sa-font-mono)' }}
      />
    </Modal>
  );
}

/** "Save settings as vN?" — diff table + optional reason (design: States). */
export function SaveSettingsDialog({
  open,
  version,
  diffs,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  version: number;
  diffs: { label: string; from: string; to: string }[];
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (open) setReason('');
  }, [open]);
  return (
    <Modal
      open={open}
      title={`Save settings as v${version}?`}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>Cancel</DialogButton>
          <DialogButton variant="accent" onClick={() => onConfirm(reason)} disabled={busy}>
            Save v{version}
          </DialogButton>
        </>
      }
    >
      <table style={{ width: '100%' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {['Setting', 'Now', 'New'].map((h) => (
              <th key={h} style={{ padding: 'var(--sa-space-2) 8px', textAlign: 'left', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {diffs.map((d) => (
            <tr key={d.label} style={{ borderBottom: '1px solid var(--sa-border)' }}>
              <td style={{ padding: 'var(--sa-space-2) 8px' }}>{d.label}</td>
              <td style={{ padding: 'var(--sa-space-2) 8px', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-muted)' }}>{d.from}</td>
              <td style={{ padding: 'var(--sa-space-2) 8px', fontFamily: 'var(--sa-font-mono)' }}>{d.to}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <input
        aria-label="Reason"
        placeholder="Reason (optional)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        style={{ height: 'var(--sa-input-height)', padding: '0 12px', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-text)' }}
      />
    </Modal>
  );
}
