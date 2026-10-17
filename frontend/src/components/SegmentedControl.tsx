import { useState } from 'react';

export interface SegmentedControlProps {
  options: string[];
  /** Controlled mode: the parent owns the selection. */
  value?: string;
  onChange?: (option: string) => void;
  label?: string;
}

export function SegmentedControl({ options, value, onChange, label }: SegmentedControlProps) {
  const [own, setOwn] = useState(options[0]);
  const selected = value ?? own;
  return (
    <div
      role="group"
      aria-label={label}
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
          aria-pressed={option === selected}
          onClick={() => (onChange ? onChange(option) : setOwn(option))}
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
