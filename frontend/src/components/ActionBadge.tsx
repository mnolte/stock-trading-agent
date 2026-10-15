export type Action = 'ENTER' | 'SKIP' | 'HOLD' | 'TIGHTEN STOP' | 'EXIT' | 'NO CALL';

const styles: Record<Action, { bg: string; border: string; color: string }> = {
  ENTER: { bg: 'var(--sa-positive-bg)', border: 'var(--sa-positive-border)', color: 'var(--sa-positive)' },
  SKIP: { bg: 'var(--sa-warning-bg)', border: 'var(--sa-warning-border)', color: 'var(--sa-warning-text)' },
  HOLD: { bg: 'var(--sa-neutral-chip-bg)', border: 'var(--sa-neutral-chip-border)', color: 'var(--sa-neutral-chip)' },
  'TIGHTEN STOP': { bg: 'var(--sa-info-bg)', border: 'var(--sa-info-border)', color: 'var(--sa-info)' },
  EXIT: { bg: 'var(--sa-negative-bg)', border: 'var(--sa-negative-border)', color: 'var(--sa-negative)' },
  'NO CALL': { bg: 'var(--sa-neutral-chip-bg)', border: 'var(--sa-neutral-chip-border)', color: 'var(--sa-neutral-chip)' },
};

export function ActionBadge({ action, size = 'small' }: { action: Action; size?: 'small' | 'large' }) {
  const s = styles[action];
  const height = size === 'large' ? '28px' : '22px';
  const padding = size === 'large' ? '0 10px' : '0 8px';
  const fontSize = size === 'large' ? 'var(--sa-text-sm)' : 'var(--sa-text-xs)';

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height,
        padding,
        borderRadius: 'var(--sa-radius-sm)',
        background: s.bg,
        border: `1px solid ${s.border}`,
        color: s.color,
        fontFamily: 'var(--sa-font-mono)',
        fontSize,
        fontWeight: 600,
        letterSpacing: '0.04em',
        whiteSpace: 'nowrap',
      }}
    >
      {action}
    </span>
  );
}
