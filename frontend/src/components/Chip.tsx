export type ChipVariant = 'neutral' | 'positive' | 'negative' | 'warning' | 'info' | 'accent';

const styles: Record<ChipVariant, { bg: string; border: string; color: string }> = {
  neutral: { bg: 'var(--sa-neutral-chip-bg)', border: 'var(--sa-neutral-chip-border)', color: 'var(--sa-neutral-chip)' },
  positive: { bg: 'var(--sa-positive-bg)', border: 'var(--sa-positive-border)', color: 'var(--sa-positive)' },
  negative: { bg: 'var(--sa-negative-bg)', border: 'var(--sa-negative-border)', color: 'var(--sa-negative)' },
  warning: { bg: 'var(--sa-warning-bg)', border: 'var(--sa-warning-border)', color: 'var(--sa-warning-text)' },
  info: { bg: 'var(--sa-info-bg)', border: 'var(--sa-info-border)', color: 'var(--sa-info)' },
  accent: { bg: 'var(--sa-accent-bg)', border: 'var(--sa-accent-border)', color: 'var(--sa-accent)' },
};

export function Chip({ variant, children }: { variant: ChipVariant; children: React.ReactNode }) {
  const s = styles[variant];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: '24px',
        padding: '0 8px',
        borderRadius: 'var(--sa-radius-sm)',
        background: s.bg,
        border: `1px solid ${s.border}`,
        color: s.color,
        fontFamily: 'var(--sa-font-mono)',
        fontSize: 'var(--sa-text-sm)',
        fontWeight: 500,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}
