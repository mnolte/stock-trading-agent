import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Dialog shell matching design/screens/States.dc.html: 480px panel,
 * negative border for destructive flows, strong border otherwise.
 * Interaction rules (design/DESIGN.md): focus moves in, stays in, Escape
 * closes (via onClose), and focus returns to what opened it. */
export function Modal({
  open,
  title,
  destructive = false,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  destructive?: boolean;
  /** Called on Escape. Pass the same handler as the Cancel button. */
  onClose?: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    // Focus moves in: the first control, else the panel itself.
    (panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCloseRef.current?.();
        return;
      }
      if (e.key !== 'Tab' || !panel) return;
      const items = panel.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        last.focus();
        e.preventDefault();
      } else if (!e.shiftKey && document.activeElement === last) {
        first.focus();
        e.preventDefault();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus();
    };
  }, [open]);

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
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
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
