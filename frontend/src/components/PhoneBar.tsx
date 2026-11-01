import { NeedsChip } from './NeedsChip';

/** Below 820 px the sidebar is replaced by this sticky top bar: app mark,
 * screen title, the Needs you count and the menu button that opens the
 * sidebar as an overlay (design/DESIGN.md — Phone width). */
export function PhoneBar({
  title,
  needsYou,
  onMenu,
  menuOpen,
}: {
  title: string;
  needsYou: number;
  onMenu: () => void;
  menuOpen: boolean;
}) {
  return (
    <div
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 5,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        height: 56,
        padding: '0 12px 0 16px',
        background: 'var(--sa-chrome)',
        borderBottom: '1px solid var(--sa-border)',
        boxSizing: 'border-box',
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 32,
          height: 32,
          flex: '0 0 32px',
          borderRadius: 8,
          background: 'var(--sa-accent)',
          color: 'var(--sa-bg)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'var(--sa-font-mono)',
          fontWeight: 600,
          fontSize: 'var(--sa-text-md)',
        }}
      >
        SA
      </div>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontWeight: 600,
          fontSize: 'var(--sa-text-lg)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {title}
      </span>
      <NeedsChip count={needsYou} compact />
      <button
        type="button"
        aria-label="Open menu"
        aria-expanded={menuOpen}
        onClick={onMenu}
        style={{
          width: 'var(--sa-control-height)',
          height: 'var(--sa-control-height)',
          flex: '0 0 var(--sa-control-height)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 'var(--sa-radius-md)',
          border: '1px solid var(--sa-border-strong)',
          background: 'var(--sa-raised)',
          color: 'var(--sa-text)',
          cursor: 'pointer',
        }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 20px' }}>
          <line x1="4" y1="7" x2="20" y2="7" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="17" x2="20" y2="17" />
        </svg>
      </button>
    </div>
  );
}
