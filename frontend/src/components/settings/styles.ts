import type { CSSProperties } from 'react';

// Design-token styles for the Settings controls (item 069).

export const inputBox = (accent = false, invalid = false): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  height: 40,
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: `1px solid ${invalid ? 'var(--sa-negative-text)' : accent ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
  background: 'var(--sa-bg)',
});

export const numInput = (width = 72): CSSProperties => ({
  width,
  height: 38,
  boxSizing: 'border-box',
  padding: '0 8px 0 10px',
  border: 0,
  background: 'transparent',
  color: 'var(--sa-text)',
  fontFamily: 'var(--sa-font-mono)',
  fontSize: 'var(--sa-text-base)',
  textAlign: 'right',
  // No outline:none — the shared :focus-visible ring stays on every
  // interactive element (design/DESIGN.md, Interaction rules).
});

export const unitText: CSSProperties = {
  padding: '0 10px 0 2px',
  fontSize: 'var(--sa-text-md)',
  color: 'var(--sa-text-muted)',
  whiteSpace: 'nowrap',
};

export const textInput = (flex?: string): CSSProperties => ({
  height: 40,
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-bg)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-base)',
  padding: '0 10px',
  flex,
});

export const selectStyle: CSSProperties = {
  ...textInput(),
  minWidth: 200,
};

export const rowFrame = (changed: boolean): CSSProperties => ({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: '8px 24px',
  padding: '14px 0',
  borderBottom: '1px solid var(--sa-border)',
  ...(changed
    ? {
        boxShadow: 'inset 3px 0 0 var(--sa-accent)',
        background: 'var(--sa-accent-bg)',
        margin: '0 -20px',
        paddingLeft: 20,
        paddingRight: 20,
      }
    : {}),
});

export const subHead: CSSProperties = {
  margin: '18px 0 0',
  fontSize: 'var(--sa-text-sm)',
  fontWeight: 600,
  color: 'var(--sa-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
};

export const chip = (tone: 'ok' | 'bad' | 'neutral'): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  height: 24,
  padding: '0 8px',
  borderRadius: 'var(--sa-radius-sm)',
  fontFamily: 'var(--sa-font-mono)',
  fontSize: 'var(--sa-text-sm)',
  whiteSpace: 'nowrap',
  ...(tone === 'ok'
    ? {
        background: 'var(--sa-positive-bg)',
        color: 'var(--sa-positive)',
        border: '1px solid var(--sa-positive-border)',
      }
    : tone === 'bad'
      ? {
          background: 'var(--sa-negative-bg)',
          color: 'var(--sa-negative-soft)',
          border: '1px solid var(--sa-negative-border)',
        }
      : {
          background: 'var(--sa-neutral-chip-bg)',
          color: 'var(--sa-neutral-chip)',
          border: '1px solid var(--sa-neutral-chip-border)',
        }),
});

