import type { ReactNode } from 'react';

export function Table({ headers, rows }: { headers: ReactNode[]; rows: ReactNode[][] }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', minWidth: '600px' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
            {headers.map((h, i) => (
              <th
                key={i}
                style={{
                  padding: 'var(--sa-space-2) 10px',
                  textAlign: i === 0 ? 'left' : 'right',
                  fontSize: 'var(--sa-text-sm)',
                  color: 'var(--sa-text-muted)',
                  fontWeight: 600,
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} style={{ borderBottom: '1px solid var(--sa-border)' }}>
              {row.map((cell, c) => (
                <td
                  key={c}
                  style={{
                    padding: 'var(--sa-space-2) 10px',
                    textAlign: c === 0 ? 'left' : 'right',
                    fontFamily: c === 0 ? 'var(--sa-font-sans)' : 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-md)',
                    color: 'var(--sa-text-2)',
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
