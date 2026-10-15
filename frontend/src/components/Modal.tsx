import type { ReactNode } from 'react';

/** Dialog shell matching design/screens/States.dc.html: 480px panel,
 * negative border for destructive flows, strong border otherwise. */
export function Modal({
  open,
  title,
  destructive = false,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  destructive?: boolean;
  children: ReactNode;
  footer: ReactNode;
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
        role="dialog"
        aria-label={title}
        style={{
          width: '480px',
          maxWidth: '100%',
          boxSizing: 'border-box',
          background: 'var(--sa-panel)',
          border: `1px solid ${destructive ? 'var(--sa-negative-border)' : 'var(--sa-border-strong)'}`,
          borderRadius: 'var(--sa-radius-xl)',
          padding: 'var(--sa-space-5) var(--sa-space-6)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sa-space-4)',
          boxShadow: '0 20px 40px rgba(0,0,0,0.45)',
        }}
      >
        <h3 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>{title}</h3>
        {children}
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: '10px' }}>
          {footer}
        </div>
      </div>
    </div>
  );
}

export type DialogButtonVariant = 'cancel' | 'danger' | 'accent';

const buttonStyles: Record<DialogButtonVariant, { bg: string; border: string; color: string }> = {
  cancel: { bg: 'transparent', border: 'var(--sa-border)', color: 'var(--sa-text-2)' },
  danger: { bg: 'var(--sa-negative-bg)', border: 'var(--sa-negative-border)', color: 'var(--sa-negative-soft)' },
  accent: { bg: 'var(--sa-accent)', border: 'var(--sa-accent)', color: 'var(--sa-bg)' },
};

export function DialogButton({
  variant,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant: DialogButtonVariant }) {
  const s = buttonStyles[variant];
  return (
    <button
      type="button"
      {...props}
      style={{
        height: 'var(--sa-control-height)',
        padding: '0 var(--sa-space-4)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--sa-space-2)',
        borderRadius: 'var(--sa-radius-md)',
        border: `1px solid ${s.border}`,
        background: s.bg,
        color: s.color,
        fontSize: 'var(--sa-text-base)',
        fontWeight: 600,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
        boxSizing: 'border-box',
      }}
    >
      {children}
    </button>
  );
}
