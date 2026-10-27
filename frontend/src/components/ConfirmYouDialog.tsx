import { useState } from 'react';

import { post, errorMessages } from '../api/client';
import type { ConfirmRequest } from '../api/auth';
import { DialogButton, Modal } from './Modal';

/**
 * "Confirm it is you" — password or authenticator code, valid
 * auth.confirm_minutes for risky actions on this device (design
 * screens/States.dc.html). Layout mounts it once; client.ts's
 * confirm_required retry drives it through `requestConfirmation`.
 * The inner form remounts per request so its inputs start clean.
 */
export function ConfirmYouDialog({ request }: { request: ConfirmRequest | null }) {
  if (!request) return null;
  return <ConfirmYouForm key={request.action + String(request)} request={request} />;
}

function ConfirmYouForm({ request }: { request: ConfirmRequest }) {
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await post('/api/auth/confirm', { secret });
      request.resolve(true);
    } catch (e) {
      setError(errorMessages(e)[0]);
      setBusy(false);
    }
  };

  const cancel = () => request.resolve(false);

  return (
    <Modal
      open
      title="Confirm it is you"
      destructive
      onClose={cancel}
      footer={
        <>
          <DialogButton variant="cancel" onClick={cancel}>
            Cancel
          </DialogButton>
          <DialogButton variant="danger" onClick={confirm} disabled={busy || !secret}>
            Confirm
          </DialogButton>
        </>
      }
    >
      <p style={{ margin: '0 0 12px', lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
        {request.action} is a risky action. Enter your password or a code from your
        authenticator to continue.
      </p>
      <label
        htmlFor="confirm-secret"
        style={{ display: 'block', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', marginBottom: 6 }}
      >
        Password or 6-digit code
      </label>
      <input
        id="confirm-secret"
        type="password"
        autoComplete="off"
        value={secret}
        onChange={(e) => setSecret(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && confirm()}
        style={{
          width: '100%',
          height: 40,
          boxSizing: 'border-box',
          borderRadius: 'var(--sa-radius-md)',
          border: '1px solid var(--sa-border)',
          background: 'var(--sa-bg)',
          color: 'var(--sa-text)',
          padding: '0 10px',
          fontSize: 'var(--sa-text-base)',
        }}
      />
      {error && (
        <p style={{ margin: '8px 0 0', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-negative)' }}>
          {error}
        </p>
      )}
      <p style={{ margin: '10px 0 0', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        Valid for 5 minutes for risky actions on this device.
      </p>
    </Modal>
  );
}
