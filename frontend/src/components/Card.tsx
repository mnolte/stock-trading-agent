import type { CSSProperties, ReactNode } from 'react';

export interface CardProps {
  label?: string;
  right?: ReactNode;
  children?: ReactNode;
  style?: CSSProperties;
  /** Sets aria-busy while the first load is in flight and announces
   * "Loading <label>" to screen readers (design/DESIGN.md → Loading). */
  busy?: boolean;
  /** A refresh of already-shown data is in flight: the 2 px accent bar
   * runs along the card's top edge (the rows dim via `.sa-stale-rows`). */
  refreshing?: boolean;
  ariaLabel?: string;
}

export function Card({ label, right, children, style, busy, refreshing, ariaLabel }: CardProps) {
  return (
    <section
      aria-label={ariaLabel}
      aria-busy={busy || refreshing || undefined}
      style={{
        position: refreshing ? 'relative' : undefined,
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
        ...style,
      }}
    >
      {refreshing && <span className="sa-refresh" aria-hidden="true" />}
      {busy && label && <span className="sa-sr">Loading {label}</span>}
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
