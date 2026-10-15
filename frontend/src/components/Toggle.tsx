import { useState } from 'react';

export function Toggle({ label }: { label: string }) {
  const [on, setOn] = useState(false);
  return (
    <button
      type="button"
      onClick={() => setOn((v) => !v)}
      aria-pressed={on}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--sa-space-3)',
        background: 'transparent',
        border: 'none',
        color: 'var(--sa-text-2)',
        cursor: 'pointer',
        fontSize: 'var(--sa-text-base)',
      }}
    >
      <span
        style={{
          width: '36px',
          height: '20px',
          borderRadius: '10px',
          background: on ? 'var(--sa-accent)' : 'var(--sa-border)',
          position: 'relative',
          transition: 'background 0.15s ease',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: '2px',
            left: on ? '18px' : '2px',
            width: '16px',
            height: '16px',
            borderRadius: '50%',
            background: 'var(--sa-text)',
            transition: 'left 0.15s ease',
          }}
        />
      </span>
      {label}
    </button>
  );
}
