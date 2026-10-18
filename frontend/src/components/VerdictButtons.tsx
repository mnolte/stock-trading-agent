import type { VerdictValue } from '../types/api';

const VERDICT_CHOICES: { value: VerdictValue; label: string }[] = [
  { value: 'good_call', label: 'Good call' },
  { value: 'bad_call', label: 'Bad call' },
  { value: 'unclear', label: 'Unclear' },
];

function buttonStyle(active: boolean): React.CSSProperties {
  return {
    height: '36px',
    padding: '0 10px',
    borderRadius: 'var(--sa-radius-sm)',
    border: `1px solid ${active ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
    background: active ? 'var(--sa-accent-bg)' : 'var(--sa-raised)',
    color: active ? 'var(--sa-accent)' : 'var(--sa-text-2)',
    fontSize: 'var(--sa-text-sm)',
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    fontFamily: 'inherit',
  };
}

/** The three verdict actions; the current value is highlighted. */
export function VerdictButtons({
  current,
  onPick,
}: {
  current: VerdictValue;
  onPick: (v: VerdictValue) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: '6px' }}>
      {VERDICT_CHOICES.map((c) => (
        <button
          key={c.value}
          type="button"
          aria-pressed={c.value === current}
          onClick={() => onPick(c.value)}
          style={buttonStyle(c.value === current)}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}
