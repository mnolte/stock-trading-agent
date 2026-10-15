import { useState } from 'react';
import { NavLink } from 'react-router-dom';

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactNode;
}

const monitor: NavItem[] = [
  {
    to: '/',
    label: 'Dashboard',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="9" rx="1.5" />
        <rect x="14" y="3" width="7" height="5" rx="1.5" />
        <rect x="14" y="12" width="7" height="9" rx="1.5" />
        <rect x="3" y="16" width="7" height="5" rx="1.5" />
      </svg>
    ),
  },
  {
    to: '/positions',
    label: 'Positions & orders',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <line x1="3" y1="10" x2="21" y2="10" />
        <line x1="9" y1="10" x2="9" y2="20" />
      </svg>
    ),
  },
  {
    to: '/performance',
    label: 'Performance',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="6" y1="20" x2="6" y2="14" />
        <line x1="12" y1="20" x2="12" y2="9" />
        <line x1="18" y1="20" x2="18" y2="4" />
      </svg>
    ),
  },
];

const learn: NavItem[] = [
  {
    to: '/inspector',
    label: 'LLM inspector',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="6" />
        <line x1="16" y1="16" x2="21" y2="21" />
      </svg>
    ),
  },
];

const control: NavItem[] = [
  {
    to: '/settings',
    label: 'Settings',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="4" y1="6" x2="20" y2="6" />
        <line x1="4" y1="12" x2="20" y2="12" />
        <line x1="4" y1="18" x2="20" y2="18" />
        <circle cx="9" cy="6" r="2" fill="currentColor" />
        <circle cx="15" cy="12" r="2" fill="currentColor" />
        <circle cx="7" cy="18" r="2" fill="currentColor" />
      </svg>
    ),
  },
];

function NavGroup({ label, expanded }: { label: string; expanded: boolean }) {
  if (!expanded) return null;
  return (
    <div
      style={{
        padding: 'var(--sa-space-2) var(--sa-space-4)',
        fontSize: 'var(--sa-text-xs)',
        color: 'var(--sa-text-muted)',
        textTransform: 'uppercase',
        letterSpacing: 'var(--sa-label-tracking)',
        fontWeight: 600,
        marginTop: 'var(--sa-space-2)',
      }}
    >
      {label}
    </div>
  );
}

function NavLinkItem({ item, expanded }: { item: NavItem; expanded: boolean }) {
  return (
    <NavLink
      to={item.to}
      title={item.label}
      style={({ isActive }) => ({
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--sa-space-3)',
        height: 'var(--sa-control-height)',
        padding: expanded ? '0 var(--sa-space-4)' : '0 calc(var(--sa-sidebar-collapsed) / 2 - 9px)',
        borderRadius: 'var(--sa-radius-md)',
        color: isActive ? 'var(--sa-text)' : 'var(--sa-text-2)',
        background: isActive ? 'var(--sa-raised)' : 'transparent',
        fontSize: 'var(--sa-text-base)',
        fontWeight: 500,
        textDecoration: 'none',
        whiteSpace: 'nowrap',
        justifyContent: expanded ? 'flex-start' : 'center',
      })}
    >
      {item.icon}
      {expanded && <span>{item.label}</span>}
    </NavLink>
  );
}

export function Sidebar() {
  const [expanded, setExpanded] = useState(true);
  const width = expanded ? 'var(--sa-sidebar-width)' : 'var(--sa-sidebar-collapsed)';

  return (
    <aside
      aria-label="Sidebar"
      style={{
        width,
        flex: `0 0 ${width}`,
        background: 'var(--sa-chrome)',
        borderRight: '1px solid var(--sa-border)',
        display: 'flex',
        flexDirection: 'column',
        padding: 'var(--sa-space-4)',
        gap: 'var(--sa-space-2)',
        transition: 'width 0.2s ease',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-3)',
          height: '52px',
          marginBottom: 'var(--sa-space-2)',
          justifyContent: expanded ? 'flex-start' : 'center',
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-accent)',
            color: 'var(--sa-bg)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'var(--sa-font-mono)',
            fontWeight: 600,
            fontSize: 'var(--sa-text-base)',
            flexShrink: 0,
          }}
        >
          SA
        </div>
        {expanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)', whiteSpace: 'nowrap' }}>Stock Agent</div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap' }}>
              Paper · Alpaca · v0.1
            </div>
          </div>
        )}
      </div>

      <nav aria-label="Main" style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
        <NavGroup label="Monitor" expanded={expanded} />
        {monitor.map((item) => (
          <NavLinkItem key={item.to} item={item} expanded={expanded} />
        ))}
        <NavGroup label="Learn" expanded={expanded} />
        {learn.map((item) => (
          <NavLinkItem key={item.to} item={item} expanded={expanded} />
        ))}
        <NavGroup label="Control" expanded={expanded} />
        {control.map((item) => (
          <NavLinkItem key={item.to} item={item} expanded={expanded} />
        ))}
      </nav>

      <div style={{ flex: 1 }} />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-2)',
          padding: expanded ? 'var(--sa-space-3) var(--sa-space-4)' : 'var(--sa-space-3) 0',
          color: 'var(--sa-text-muted)',
          fontSize: 'var(--sa-text-sm)',
          justifyContent: expanded ? 'flex-start' : 'center',
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: 'var(--sa-positive)',
            flexShrink: 0,
          }}
        />
        {expanded && <span>Running · paper</span>}
      </div>

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-label={expanded ? 'Collapse' : 'Expand'}
        aria-expanded={expanded}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-3)',
          height: 'var(--sa-control-height)',
          padding: expanded ? '0 var(--sa-space-4)' : '0 calc(var(--sa-sidebar-collapsed) / 2 - 9px)',
          borderRadius: 'var(--sa-radius-md)',
          background: 'transparent',
          border: 'none',
          color: 'var(--sa-text-2)',
          cursor: 'pointer',
          fontSize: 'var(--sa-text-base)',
          fontWeight: 500,
          justifyContent: expanded ? 'flex-start' : 'center',
        }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <line x1="9" y1="4" x2="9" y2="20" />
        </svg>
        {expanded && <span>Collapse</span>}
      </button>
    </aside>
  );
}
