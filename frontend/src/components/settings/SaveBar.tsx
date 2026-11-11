import { useState } from 'react';

import { DialogButton, Modal } from '../Modal';
import { Button } from '../Button';
import { textInput } from './styles';
import type { ChangeDirection } from '../../pages/settingsDiff';

// The unsaved-changes bar (item 069): every change is tagged tighter or
// looser; a save with looser changes asks for confirmation first and
// names only those changes. Tighter-only saves skip the dialog.

export interface BarChange {
  key: string;
  text: string;
  direction: ChangeDirection | null;
  /** "Raises risk per trade 0.5% → 0.6%" — set only for looser changes. */
  phrase: string | null;
}

const dirColor = (dir: ChangeDirection): string =>
  dir === 'tighter' ? 'var(--sa-positive)' : 'var(--sa-accent)';

export function SaveBar({
  changes,
  reason,
  onReason,
  onDiscard,
  onSave,
  saving,
  invalid,
  nextVersion,
}: {
  changes: BarChange[];
  reason: string;
  onReason: (value: string) => void;
  onDiscard: () => void;
  onSave: () => void;
  saving: boolean;
  invalid: boolean;
  nextVersion: number;
}) {
  const [confirming, setConfirming] = useState(false);
  const looser = changes.filter((c) => c.direction === 'looser');

  const requestSave = () => {
    if (looser.length > 0) setConfirming(true);
    else onSave();
  };

  return (
    <>
      <div
        role="region"
        aria-label="Unsaved changes"
        style={{
          position: 'sticky',
          bottom: 16,
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '12px 16px',
          padding: '12px 16px',
          background: 'var(--sa-chrome)',
          border: '1px solid var(--sa-accent-border)',
          borderRadius: 'var(--sa-radius-lg)',
          boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
        }}
      >
        <div style={{ flex: '2 1 320px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 'var(--sa-text-base)', fontWeight: 600 }}>
            {changes.length} unsaved change{changes.length === 1 ? '' : 's'}
          </span>
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px' }}>
            {changes.map((c) => (
              <span
                key={c.key}
                style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}
              >
                {c.text}
                {c.direction && (
                  <span
                    style={{
                      marginLeft: 6,
                      fontSize: 'var(--sa-text-xs)',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '0.04em',
                      color: dirColor(c.direction),
                    }}
                  >
                    {c.direction}
                  </span>
                )}
              </span>
            ))}
          </span>
        </div>
        <input
          aria-label="Reason for this change"
          placeholder="Reason (shown in history)"
          value={reason}
          onChange={(e) => onReason(e.target.value)}
          style={{ ...textInput('1 1 220px'), borderColor: reason.trim() ? 'var(--sa-border-strong)' : 'var(--sa-accent-border)' }}
        />
        <Button onClick={onDiscard}>Discard</Button>
        <Button
          variant="primary"
          onClick={requestSave}
          disabled={saving || invalid || !reason.trim()}
          title={
            invalid
              ? 'Fix the invalid values first'
              : !reason.trim()
                ? 'A reason is required — it shows in History'
                : undefined
          }
        >
          {saving ? 'Saving…' : `Save as v${nextVersion}`}
        </Button>
      </div>
      <Modal
        open={confirming}
        title={`Looser changes — save as v${nextVersion}?`}
        onClose={() => setConfirming(false)}
        footer={
          <>
            <DialogButton variant="cancel" onClick={() => setConfirming(false)}>
              Back
            </DialogButton>
            <DialogButton
              variant="accent"
              onClick={() => {
                setConfirming(false);
                onSave();
              }}
              disabled={saving}
            >
              Save as v{nextVersion}
            </DialogButton>
          </>
        }
      >
        <p style={{ margin: '0 0 8px', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
          These changes loosen the agent:
        </p>
        <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--sa-text)', lineHeight: 1.6 }}>
          {looser.map((c) => (
            <li key={c.key}>{c.phrase ?? c.text}</li>
          ))}
        </ul>
      </Modal>
    </>
  );
}
