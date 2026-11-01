import { Link } from 'react-router-dom';

/** The "Needs you · n" count — amber pill with a warning triangle, links
 * to the dashboard's Needs you card (design: header + phone bar). */
export function NeedsChip({
  count,
  compact = false,
}: {
  count: number;
  /** The phone bar's number-only variant. */
  compact?: boolean;
}) {
  if (count <= 0) return null;
  return (
    <Link
      to="/#needs"
      aria-label={`${count} item${count === 1 ? '' : 's'} need${count === 1 ? 's' : ''} you`}
      title="Needs you"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: compact ? 4 : 6,
        height: 32,
        padding: compact ? '0 10px' : '0 12px',
        borderRadius: 999,
        background: 'var(--sa-warning-bg)',
        border: '1px solid var(--sa-warning-border)',
        color: 'var(--sa-warning-text)',
        fontFamily: compact ? 'var(--sa-font-mono)' : undefined,
        fontSize: 'var(--sa-text-md)',
        fontWeight: 500,
        textDecoration: 'none',
        whiteSpace: 'nowrap',
      }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
        <path d="M12 3l10 18H2z" />
        <line x1="12" y1="10" x2="12" y2="14" />
        <line x1="12" y1="18" x2="12" y2="18" />
      </svg>
      {compact ? count : <span>Needs you · {count}</span>}
    </Link>
  );
}
