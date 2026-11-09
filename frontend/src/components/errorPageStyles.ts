import type React from 'react';

/** Button styles shared by the error pages and the screen boundary.
 * Kept out of the .tsx files so they stay component-only exports. */
export const accentButton: React.CSSProperties = {
  height: 40,
  padding: '0 16px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-accent)',
  background: 'var(--sa-accent)',
  color: '#0B0E13',
  fontSize: 'var(--sa-text-base)',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  boxSizing: 'border-box',
};

export const ghostButton: React.CSSProperties = {
  height: 40,
  padding: '0 16px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border)',
  background: 'transparent',
  color: 'var(--sa-text-2)',
  fontSize: 'var(--sa-text-base)',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  boxSizing: 'border-box',
};

/** The small warning-outline button of the card states (Retry, Reload
 * card, Open in Logs) — design/screens/States.dc.html. */
export const warningButton: React.CSSProperties = {
  height: 36,
  padding: '0 var(--sa-space-3)',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-warning-border)',
  background: 'transparent',
  color: 'var(--sa-warning-text)',
  fontSize: 'var(--sa-text-md)',
  fontWeight: 500,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};
