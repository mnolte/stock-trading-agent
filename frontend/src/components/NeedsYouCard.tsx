import { useAsk } from '../ask/AskContext';
import { Link } from 'react-router-dom';

import type { AttentionItem, AttentionResponse } from '../types/api';
import type { Resource } from '../api/useResource';
import { displayTime, signed } from '../format';
import { CardBody } from './cardState';
import { CardBoundary } from './CardBoundary';
import { SkeletonText } from './skeletons';

const ICONS = {
  warn: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 16px' }}>
      <path d="M12 3l10 18H2z" /><line x1="12" y1="10" x2="12" y2="14" /><line x1="12" y1="18" x2="12" y2="18" />
    </svg>
  ),
  check: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 16px' }}>
      <polyline points="5 12 10 17 19 7" />
    </svg>
  ),
};

function icon(severity: AttentionItem['severity']) {
  return (
    <span
      style={{
        color:
          severity === 'info'
            ? 'var(--sa-info)'
            : severity === 'critical'
              ? 'var(--sa-negative-text)'
              : 'var(--sa-warning-text)',
        display: 'flex',
        paddingTop: 2,
      }}
    >
      {severity === 'info' ? ICONS.check : ICONS.warn}
    </span>
  );
}

/** "18:46:03" — Amsterdam time with seconds (the health line's sync stamp). */
function hms(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

function Dot({ color }: { color: string }) {
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flex: '0 0 8px' }} />;
}

function HealthLine({ health }: { health: AttentionResponse['health'] }) {
  const ok = 'var(--sa-positive)';
  const warn = 'var(--sa-warning-text)';
  const bad = 'var(--sa-negative-text)';
  const muted = 'var(--sa-text-muted)';
  const entries: [string, string, string][] = [
    ['Alpaca', health.alpaca.ok ? `in sync${health.alpaca.at ? ` ${hms(health.alpaca.at)}` : ''}` : 'mismatch', health.alpaca.ok ? ok : bad],
    ['Order stream', health.stream, health.stream === 'connected' ? ok : health.stream === 'unknown' ? muted : warn],
    [
      'Clock',
      `${signed((health.clock.offset_ms ?? 0) / 1000, 1)} s`,
      health.clock.level === 'ok' ? ok : health.clock.level === 'warning' ? warn : muted,
    ],
    [
      'Keys',
      health.keys.status === 'passing' ? 'all passing' : health.keys.detail,
      health.keys.status === 'passing' ? ok : warn,
    ],
    [
      'Email',
      health.email === 'failing'
        ? `failing${health.email_since ? ` since ${displayTime(health.email_since)}` : ''}`
        : health.email,
      health.email === 'failing' ? warn : health.email === 'off' ? muted : ok,
    ],
  ];
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px', padding: '10px 0 4px', borderTop: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-sm)' }}>
      {entries.map(([label, value, color]) => (
        <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <Dot color={color} />
          <span style={{ color: 'var(--sa-text-muted)' }}>{label}</span>{' '}
          <span style={{ color: color === ok || color === muted ? 'var(--sa-text-2)' : color }}>{value}</span>
        </span>
      ))}
    </div>
  );
}

/** The dashboard's first card (docs/SPEC.md, GUI — Needs you): one list,
 * most urgent first; items leave when done, not when seen. Empty shows
 * "Nothing needs you" plus the always-on health line. */
export function NeedsYouCard({ res }: { res: Resource<AttentionResponse> }) {
  const data = res.data;
  const ask = useAsk();
  const items = data?.items ?? [];
  return (
    <section
      id="needs"
      aria-label="Needs you"
      aria-busy={res.loading || res.refreshing || undefined}
      style={{
        position: res.refreshing ? 'relative' : undefined,
        background: 'var(--sa-panel)',
        border: `1px solid ${items.length ? 'var(--sa-warning-border)' : 'var(--sa-border)'}`,
        borderLeft: items.length ? '3px solid var(--sa-warning-text)' : undefined,
        borderRadius: 'var(--sa-radius-lg)',
        padding: '12px 16px 8px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      {res.refreshing && <span className="sa-refresh" aria-hidden="true" />}
      {res.loading && <span className="sa-sr">Loading Needs you</span>}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px 12px', paddingBottom: 6 }}>
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
          Needs you{' '}
          {items.length > 0 && (
            <span style={{ marginLeft: 6, padding: '1px 7px', borderRadius: 999, background: 'var(--sa-warning-bg)', color: 'var(--sa-warning-text)', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)' }}>
              {items.length}
            </span>
          )}
        </h2>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          most urgent first · items leave when they're done
        </span>
      </div>
      <CardBoundary name="Needs you">
      <CardBody
        res={res}
        what="the Needs you list"
        skeleton={
          <>
            <SkeletonText lines={3} />
            <div
              aria-hidden="true"
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '8px 18px',
                paddingTop: 8,
                borderTop: '1px solid var(--sa-border)',
              }}
            >
              {[120, 90, 140, 80].map((w, i) => (
                <span
                  key={i}
                  className="sa-skel"
                  style={{ display: 'inline-block', height: 10, width: w, borderRadius: 4 }}
                />
              ))}
            </div>
          </>
        }
        isEmpty={() => false}
        empty={null}
      >
        {() => (
          <>
            {items.length === 0 && (
              <div style={{ padding: '6px 0', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                Nothing needs you.
              </div>
            )}
            {items.map((item, i) => (
              <div
                key={`${item.kind}-${i}`}
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: '8px 12px',
                  minHeight: 'var(--sa-control-height)',
                  padding: '6px 0',
                  borderTop: '1px solid var(--sa-border)',
                }}
              >
                <span style={{ flex: '1 1 300px', minWidth: 0, display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 'var(--sa-text-md)', lineHeight: 1.5 }}>
                  {icon(item.severity)}
                  <span>
                    <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>{item.text}</b>{' '}
                    <span style={{ color: 'var(--sa-text-muted)' }}>{item.detail}</span>
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() =>
                    ask.openDrawer({
                      screen: 'dashboard',
                      label: item.text,
                      summary: `${item.text} — ${item.detail}`,
                      route: item.link,
                    })
                  }
                  title="Ask the agent about this item"
                  aria-label="Ask the agent about this item"
                  style={{
                    height: 36,
                    padding: '0 10px',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    borderRadius: 'var(--sa-radius-sm)',
                    border: '1px solid var(--sa-border)',
                    background: 'transparent',
                    color: 'var(--sa-text-muted)',
                    fontSize: 'var(--sa-text-md)',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
                    <path d="M4 5h16v11H9l-5 4z" />
                    <line x1="8" y1="9" x2="16" y2="9" />
                    <line x1="8" y1="12" x2="13" y2="12" />
                  </svg>
                  Ask
                </button>
                <Link
                  to={item.link}
                  style={{
                    height: 36,
                    padding: '0 12px',
                    display: 'inline-flex',
                    alignItems: 'center',
                    borderRadius: 'var(--sa-radius-sm)',
                    border: '1px solid var(--sa-border-strong)',
                    background: 'var(--sa-raised)',
                    color: 'var(--sa-text)',
                    fontSize: 'var(--sa-text-md)',
                    fontWeight: 500,
                    textDecoration: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.action_label}
                </Link>
              </div>
            ))}
            {data && <HealthLine health={data.health} />}
          </>
        )}
      </CardBody>
      </CardBoundary>
    </section>
  );
}
