import { useState } from 'react';

export function SegmentedControl({ options }: { options: string[] }) {
  const [selected, setSelected] = useState(options[0]);
  return (
    <div
      style={{
        display: 'inline-flex',
        background: 'var(--sa-raised)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        padding: '2px',
      }}
    >
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => setSelected(option)}
          style={{
            height: '32px',
            padding: '0 var(--sa-space-4)',
            borderRadius: 'var(--sa-radius-sm)',
            background: option === selected ? 'var(--sa-panel)' : 'transparent',
            border: 'none',
            color: option === selected ? 'var(--sa-text)' : 'var(--sa-text-muted)',
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
