import type { ReactNode } from 'react';

export interface StatCardProps {
  label: ReactNode;
  value: string;
  valueColor?: string;
  sub?: string;
  bar?: { width: string; color: string };
  /** Pinned to the tile's bottom edge (item 073: the "Open →" link). */
  footer?: ReactNode;
  style?: React.CSSProperties;
}

export function StatCard({ label, value, valueColor, sub, bar, footer, style }: StatCardProps) {
  return (
    <div
      style={{
        flex: '1 1 190px',
        padding: 'var(--sa-space-4)',
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        minWidth: 0,
        ...style,
      }}
    >
      <div
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--sa-label-tracking)',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-stat)',
          fontWeight: 500,
          color: valueColor ?? 'var(--sa-text)',
          overflowWrap: 'anywhere',
        }}
      >
        {value}
      </div>
      {bar && (
        <div style={{ height: '6px', background: 'var(--sa-border)', borderRadius: '3px', overflow: 'hidden' }}>
          <div className="sa-bar-fill" style={{ width: bar.width, height: '6px', background: bar.color }} />
        </div>
      )}
      {sub && <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>{sub}</div>}
      {footer && (
        <div
          style={{
            alignSelf: 'flex-end',
            marginTop: 'auto',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-accent)',
          }}
        >
          {footer}
        </div>
      )}
    </div>
  );
}
