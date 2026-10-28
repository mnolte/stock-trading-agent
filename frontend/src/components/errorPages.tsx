/**
 * The in-app error pages (design/screens/Errors.dc.html — the texts are
 * final): route and record not-found, "The app isn't answering" on first
 * load, the backend banner after a successful load, and the offline
 * banner. The static Caddy pages live in deploy/errors/.
 */
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { retryNow, useBackendStatus } from '../api/backend';
import { displayTime } from '../format';
import { accentButton } from './errorPageStyles';

const mono: React.CSSProperties = {
  fontFamily: 'var(--sa-font-mono)',
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
};

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 18px' }}>
      <circle cx="11" cy="11" r="6" />
      <line x1="16" y1="16" x2="21" y2="21" />
    </svg>
  );
}

function PlugIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 18px' }}>
      <path d="M9 2v6M15 2v6" />
      <path d="M6 8h12v3a6 6 0 0 1-12 0z" />
      <line x1="12" y1="17" x2="12" y2="22" />
    </svg>
  );
}

function ShieldNote() {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '10px 12px', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-panel)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 16px', color: 'var(--sa-positive, #3DD68C)', marginTop: 2 }}>
        <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        <polyline points="9 12 11 14 15 10" />
      </svg>
      <span>Open positions keep their stops and targets at Alpaca, whatever this page shows.</span>
    </div>
  );
}

function IconTile({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span style={{ width: 36, height: 36, borderRadius: 8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color }}>
      {children}
    </span>
  );
}

export function PrimaryLink({ to, children }: { to: string; children: string }) {
  return (
    <Link
      to={to}
      style={{ height: 40, padding: '0 16px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-accent)', background: 'var(--sa-accent)', color: '#0B0E13', fontSize: 'var(--sa-text-base)', fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap', boxSizing: 'border-box' }}
    >
      {children}
    </Link>
  );
}

export function GhostLink({ to, children }: { to: string; children: string }) {
  return (
    <Link
      to={to}
      style={{ height: 40, padding: '0 16px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'transparent', color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-base)', fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap', boxSizing: 'border-box' }}
    >
      {children}
    </Link>
  );
}

export function BugIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 18px' }}>
      <rect x="8" y="6" width="8" height="14" rx="4" />
      <line x1="12" y1="10" x2="12" y2="20" />
      <line x1="4" y1="12" x2="8" y2="12" />
      <line x1="16" y1="12" x2="20" y2="12" />
      <line x1="5" y1="7" x2="8" y2="9" />
      <line x1="19" y1="7" x2="16" y2="9" />
    </svg>
  );
}

function ErrorPanel({ badge, title, note, actions, extra }: {
  badge: React.ReactNode;
  title: string;
  note: string;
  actions?: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'var(--sa-space-6)' }}>
      <div style={{ maxWidth: 440, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>{badge}</div>
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>{title}</h2>
        <p style={{ margin: 0, fontSize: 'var(--sa-text-base)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>{note}</p>
        {extra}
        {actions && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>{actions}</div>
        )}
      </div>
    </div>
  );
}

/** Unknown route (design: "Not found · a page"). When sign-in is
 * required but the visitor isn't signed in, the route goes to sign-in
 * instead — nothing is revealed. Handled by the caller via `signedIn`. */
export function RouteNotFound({ signedIn }: { signedIn: boolean }) {
  const navigate = useNavigate();
  useEffect(() => {
    if (!signedIn) {
      const next = window.location.pathname + window.location.search;
      navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
    }
  }, [signedIn, navigate]);
  if (!signedIn) return null;
  return (
    <ErrorPanel
      badge={<><IconTile color="var(--sa-text-muted)"><SearchIcon /></IconTile><span style={mono}>404 · not found</span></>}
      title="There is no page here"
      note="Check the address, or pick a screen from the menu."
      actions={<PrimaryLink to="/">Go to Dashboard</PrimaryLink>}
    />
  );
}

/** A record id that doesn't exist (design: "Not found · a record"). */
export function RecordNotFound({ what, why, searchTo, searchLabel }: {
  /** "Decision 98213 doesn't exist" */
  what: string;
  /** "Decisions are never deleted within a run." */
  why: string;
  searchTo: string;
  searchLabel: string;
}) {
  return (
    <ErrorPanel
      badge={<><IconTile color="var(--sa-text-muted)"><SearchIcon /></IconTile><span style={mono}>404 · not found</span></>}
      title={what}
      note={`It may be from a run that was reset, or the link is mistyped. ${why}`}
      actions={
        <>
          <PrimaryLink to="/">Go to Dashboard</PrimaryLink>
          <GhostLink to={searchTo}>{searchLabel}</GhostLink>
        </>
      }
    />
  );
}

/** First load can't reach the API (design: "API not answering on first
 * load"). Retries every 15 s; the badge counts down to the next probe. */
export function AppNotAnswering() {
  const { retryAt } = useBackendStatus();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, []);
  const seconds = retryAt ? Math.max(0, Math.ceil((retryAt.getTime() - now) / 1000)) : null;
  return (
    <ErrorPanel
      badge={
        <>
          <IconTile color="var(--sa-warning-text)"><PlugIcon /></IconTile>
          <span style={mono}>
            {seconds === null ? 'no response' : `no response · retrying in ${seconds} s`}
          </span>
        </>
      }
      title="The app isn't answering"
      note="This page loaded, but the agent's API didn't respond. It may be restarting. Trying again every 15 seconds."
      extra={<ShieldNote />}
      actions={
        <button type="button" style={accentButton} onClick={retryNow}>
          Try now
        </button>
      }
    />
  );
}

/** The page-level backend banner (design: States → Backend unreachable):
 * after a successful load, requests that get no answer show this; it
 * retries every 15 seconds. */
export function BackendDownBanner({ since }: { since: Date }) {
  return (
    <div
      role="status"
      style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--sa-space-3)', padding: '12px 20px', background: 'var(--sa-negative-bg)', border: '1px solid var(--sa-negative-border)', borderRadius: 'var(--sa-radius-md)', color: 'var(--sa-negative-soft)', fontSize: 'var(--sa-text-md)', marginBottom: 'var(--sa-space-4)' }}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 16px' }}>
        <path d="M12 3l10 18H2z" />
        <line x1="12" y1="10" x2="12" y2="14" />
        <line x1="12" y1="18" x2="12" y2="18" />
      </svg>
      <span style={{ flex: '1 1 420px', lineHeight: 1.5 }}>
        <b>Can’t reach the Stock Trading Agent backend since {displayTime(since.toISOString())}.</b>{' '}
        Retrying every 15 seconds. Stops at Alpaca keep protecting positions.
      </span>
      <button
        type="button"
        onClick={retryNow}
        style={{ height: 36, padding: '0 12px', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-negative-border)', background: 'transparent', color: 'var(--sa-negative-soft)', fontSize: 'var(--sa-text-md)', fontWeight: 500, cursor: 'pointer', whiteSpace: 'nowrap' }}
      >
        Retry now
      </button>
    </div>
  );
}

/** The browser reports no network: requests pause until it's back. */
export function OfflineBanner() {
  return (
    <div
      role="status"
      style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)', padding: '12px 20px', background: 'var(--sa-warning-bg)', border: '1px solid var(--sa-warning-border)', borderRadius: 'var(--sa-radius-md)', color: 'var(--sa-warning-text)', fontSize: 'var(--sa-text-md)', marginBottom: 'var(--sa-space-4)' }}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 16px' }}>
        <line x1="2" y1="2" x2="22" y2="22" />
        <path d="M8.5 16.5a5 5 0 0 1 7 0" />
        <path d="M2 8.82a15 15 0 0 1 4.17-2.65" />
        <path d="M10.66 5c4.01-.36 8.14.9 11.34 3.76" />
        <path d="M16.85 11.25a10 10 0 0 1 2.22 1.68" />
        <path d="M5 13a10 10 0 0 1 5.24-2.76" />
        <line x1="12" y1="20" x2="12.01" y2="20" />
      </svg>
      <span style={{ flex: 1, lineHeight: 1.5 }}>
        <b>You’re offline.</b> Requests pause until the network is back.
      </span>
    </div>
  );
}
