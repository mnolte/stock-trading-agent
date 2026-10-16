import type { ReactNode } from 'react';

export interface StatCardProps {
  label: ReactNode;
  value: string;
  valueColor?: string;
  sub?: string;
  bar?: { width: string; color: string };
}

export function StatCard({ label, value, valueColor, sub, bar }: StatCardProps) {
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
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </div>
      {bar && (
        <div style={{ height: '6px', background: 'var(--sa-border)', borderRadius: '3px', overflow: 'hidden' }}>
          <div style={{ width: bar.width, height: '6px', background: bar.color }} />
        </div>
      )}
      {sub && <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>{sub}</div>}
    </div>
  );
}
