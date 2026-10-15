import type { ReactNode } from 'react';

export type BannerVariant = 'negative' | 'warning' | 'info';

const styles: Record<BannerVariant, { bg: string; border: string; color: string }> = {
  negative: { bg: 'var(--sa-negative-bg)', border: 'var(--sa-negative-border)', color: 'var(--sa-negative-soft)' },
  warning: { bg: 'var(--sa-warning-bg)', border: 'var(--sa-warning-border)', color: 'var(--sa-warning-text)' },
  info: { bg: 'var(--sa-info-bg)', border: 'var(--sa-info-border)', color: 'var(--sa-info)' },
};

export function Banner({ variant, children, actions }: { variant: BannerVariant; children: ReactNode; actions?: ReactNode }) {
  const s = styles[variant];
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 'var(--sa-space-3)',
        padding: 'var(--sa-space-3) var(--sa-space-4)',
        background: s.bg,
        border: `1px solid ${s.border}`,
        borderRadius: 'var(--sa-radius-md)',
        color: s.color,
      }}
    >
      <span style={{ flex: '1 1 240px', lineHeight: 1.5 }}>{children}</span>
      {actions && <div style={{ display: 'flex', gap: 'var(--sa-space-2)' }}>{actions}</div>}
    </div>
  );
}
