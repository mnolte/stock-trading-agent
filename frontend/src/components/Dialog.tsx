import type { ReactNode } from 'react';

export function Dialog({
  open,
  title,
  children,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
        padding: 'var(--sa-space-4)',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '420px',
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-xl)',
          padding: 'var(--sa-space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sa-space-4)',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-lg)', fontWeight: 600 }}>{title}</h2>
        <div style={{ color: 'var(--sa-text-2)' }}>{children}</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--sa-space-3)' }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              height: 'var(--sa-control-height)',
              padding: '0 var(--sa-space-4)',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border-strong)',
              color: 'var(--sa-text)',
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            style={{
              height: 'var(--sa-control-height)',
              padding: '0 var(--sa-space-4)',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-negative)',
              border: '1px solid var(--sa-negative)',
              color: 'var(--sa-bg)',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}
