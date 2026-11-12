import type { CSSProperties } from 'react';

/**
 * "The numbers" fold (item 079, design screens/Main.dc.html and
 * Reviews.dc.html): every fact the story's model was given, as
 * label–value rows. The backend builds the rows; the GUI only renders.
 */
export interface NumberRow {
  label: string;
  value: string;
}

const rowStyle: CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  gap: 'var(--sa-space-3)',
  padding: '6px 0',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
};

export function NumbersFold({
  rows,
  factCount,
}: {
  rows: NumberRow[];
  /** Shown in the summary when known ("9 facts the story was given"). */
  factCount?: number;
}) {
  if (rows.length === 0) return null;
  return (
    <details
      style={{
        borderTop: '1px solid var(--sa-border)',
        paddingTop: 'var(--sa-space-2)',
        fontSize: 'var(--sa-text-md)',
        color: 'var(--sa-text-muted)',
      }}
    >
      <summary
        style={{
          cursor: 'pointer',
          minHeight: 'var(--sa-control-height)',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        The numbers
        {factCount !== undefined ? ` · ${factCount} facts the story was given` : ''}
      </summary>
      <div style={{ padding: 'var(--sa-space-1) 0 var(--sa-space-2)' }}>
        {rows.map((row, i) => (
          <div key={`${row.label}-${i}`} style={rowStyle}>
            <span style={{ color: 'var(--sa-text-muted)' }}>{row.label}</span>
            <span
              style={{
                fontFamily: 'var(--sa-font-mono)',
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-2)',
                textAlign: 'right',
              }}
            >
              {row.value}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}
