import { Link } from 'react-router-dom';

import { dismissApiToast, useApiToasts } from '../api/toasts';

/** 500 → toast with the error id and "Open in Logs"; 409 → says what
 * changed and offers to reload (docs/SPEC.md, status table). */
export default function ApiErrorToasts() {
  const toasts = useApiToasts();
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      style={{ position: 'fixed', right: 'var(--sa-space-5)', bottom: 'var(--sa-space-5)', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-2)', zIndex: 60, maxWidth: 420 }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="alert"
          style={{ padding: '12px 16px', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-negative-bg)', border: '1px solid var(--sa-negative-border)', color: 'var(--sa-negative-soft)', fontSize: 'var(--sa-text-md)', lineHeight: 1.5, display: 'flex', flexDirection: 'column', gap: 8 }}
        >
          <span>
            {t.message}
            {t.errorId && (
              <>
                {' '}Error id <code style={{ fontFamily: 'var(--sa-font-mono)' }}>{t.errorId}</code>.
              </>
            )}
          </span>
          <span style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            {t.status === 500 && (
              <Link to="/logs" style={{ color: 'var(--sa-negative-soft)', fontWeight: 600 }}>
                Open in Logs
              </Link>
            )}
            {t.status === 409 && (
              <button
                type="button"
                onClick={() => window.location.reload()}
                style={{ height: 30, padding: '0 12px', borderRadius: 'var(--sa-radius-sm)', border: '1px solid currentColor', background: 'transparent', color: 'inherit', fontWeight: 600, cursor: 'pointer' }}
              >
                Reload
              </button>
            )}
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismissApiToast(t.id)}
              style={{ marginLeft: 'auto', border: 'none', background: 'none', color: 'inherit', cursor: 'pointer', fontSize: 'var(--sa-text-md)' }}
            >
              Dismiss
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}
