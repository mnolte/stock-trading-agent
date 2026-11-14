import type { CSSProperties } from 'react';

const style: CSSProperties = {
  margin: 0,
  padding: '6px 10px',
  borderRadius: 'var(--sa-radius-sm)',
  background: 'var(--sa-subtle)',
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
};

/**
 * The briefing's code-written status line (design screens/Main.dc.html,
 * Reviews.dc.html): a subtle strip between the title and the story.
 * Written once by code; the GUI only renders it.
 */
export function StatusLine({ text }: { text: string }) {
  return <p style={style}>{text}</p>;
}
