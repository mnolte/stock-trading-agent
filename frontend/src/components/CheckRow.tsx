export type CheckVariant = 'pass' | 'fail' | 'adjusted';

const icons: Record<CheckVariant, string> = {
  pass: '✓',
  fail: '✕',
  adjusted: '~',
};

const colors: Record<CheckVariant, string> = {
  pass: 'var(--sa-positive)',
  fail: 'var(--sa-negative)',
  adjusted: 'var(--sa-warning-text)',
};

export function CheckRow({ label, variant }: { label: string; variant: CheckVariant }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 'var(--sa-space-3)',
        padding: '6px 0',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <span>{label}</span>
      <span style={{ color: colors[variant], fontWeight: 600 }}>{icons[variant]}</span>
    </div>
  );
}
