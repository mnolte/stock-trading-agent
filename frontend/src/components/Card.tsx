import type { ReactNode } from 'react';

export interface CardProps {
  label?: string;
  right?: ReactNode;
  children?: ReactNode;
}

export function Card({ label, right, children }: CardProps) {
  return (
    <section
      style={{
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
      }}
    >
      {(label || right) && (
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
          <h2
            style={{
              margin: 0,
              fontSize: 'var(--sa-text-sm)',
              fontWeight: 600,
              color: 'var(--sa-text-2)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            {label}
          </h2>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}
