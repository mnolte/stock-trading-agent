import { useEffect, useState } from 'react';

import type { HeaderState } from '../types/api';
import { sessionLabel, sessionTail } from './sessionText';

function sessionDot(state: string) {
  switch (state) {
    case 'open':
      return 'var(--sa-positive)';
    case 'last_cycle_done':
      return 'var(--sa-warning-text)';
    default:
      return 'var(--sa-text-muted)';
  }
}

export function Header({
  state,
  title,
  onKillSwitch,
  onMenu,
}: {
  state: HeaderState | null;
  title: string;
  onKillSwitch: () => void;
  onMenu?: () => void;
}) {
  const [, forceTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(timer);
  }, []);

  const sessionState = state?.session.state ?? 'closed';
  const tail = state ? sessionTail(sessionState, state.session) : '';

  return (
    <header
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 'var(--sa-space-3) var(--sa-space-5)',
        padding: '14px var(--sa-space-6)',
        borderBottom: '1px solid var(--sa-border)',
        background: 'var(--sa-chrome)',
      }}
    >
      {onMenu && (
        <button
          type="button"
          aria-label="Menu"
          onClick={onMenu}
          style={{
            height: 'var(--sa-control-height)',
            width: 'var(--sa-control-height)',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border)',
            background: 'transparent',
            color: 'var(--sa-text-2)',
            cursor: 'pointer',
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="4" y1="7" x2="20" y2="7" />
            <line x1="4" y1="12" x2="20" y2="12" />
            <line x1="4" y1="17" x2="20" y2="17" />
          </svg>
        </button>
      )}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-3)',
          flex: '1 1 300px',
          minWidth: 0,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>{title}</h1>
          <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            {state?.session.display_time ?? '—'}
          </div>
        </div>

        <span
          title="Orders go to Alpaca's paper account. Real-money trading is not built."
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            height: '26px',
            padding: '0 10px',
            borderRadius: 'var(--sa-radius-sm)',
            background: 'var(--sa-accent-bg)',
            border: '1px solid var(--sa-accent-border)',
            color: 'var(--sa-accent)',
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 600,
            letterSpacing: '0.08em',
          }}
        >
          PAPER
        </span>

        <div
          title="Market session state"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--sa-space-3)',
            height: '32px',
            padding: '0 12px',
            borderRadius: '999px',
            background: 'var(--sa-raised)',
            border: '1px solid var(--sa-border)',
            fontSize: 'var(--sa-text-md)',
            whiteSpace: 'nowrap',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: sessionDot(sessionState),
            }}
          />
          <span>
            {sessionLabel(sessionState)}
            <span style={{ color: 'var(--sa-text-muted)' }}>{tail}</span>
          </span>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 'var(--sa-space-3) var(--sa-space-5)',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
          <span
            style={{
              fontSize: 'var(--sa-text-xs)',
              color: 'var(--sa-text-muted)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            Equity
          </span>
          <span
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-base)',
              color: 'var(--sa-text)',
              whiteSpace: 'nowrap',
            }}
          >
            ${state?.equity_usd ?? '—'} <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>€{state?.equity_eur ?? ''}</span>
          </span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
          <span
            style={{
              fontSize: 'var(--sa-text-xs)',
              color: 'var(--sa-text-muted)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            Today
          </span>
          <span
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-base)',
              color: 'var(--sa-positive)',
              whiteSpace: 'nowrap',
            }}
          >
            {state?.today_usd ?? '—'} <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>{state?.today_eur ?? ''}</span>
          </span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span
            title="Market regime from SPY: trend × volatility"
            style={{
              fontSize: 'var(--sa-text-xs)',
              color: 'var(--sa-text-muted)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
              textDecoration: 'underline dotted',
              textUnderlineOffset: '3px',
              cursor: 'help',
              alignSelf: 'flex-start',
            }}
          >
            Regime
          </span>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: '24px',
              padding: '0 8px',
              borderRadius: 'var(--sa-radius-sm)',
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-sm)',
              whiteSpace: 'nowrap',
              background: 'var(--sa-positive-bg)',
              color: 'var(--sa-positive)',
              border: '1px solid var(--sa-positive-border)',
            }}
          >
            {state?.regime ?? '—'}
          </span>
        </div>

        <button
          type="button"
          onClick={onKillSwitch}
          title={state?.kill_switch ? 'Resume trading' : 'Stop new entries, or stop and sell everything'}
          style={{
            height: 'var(--sa-control-height)',
            padding: '0 var(--sa-space-4)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-negative-border)',
            background: state?.kill_switch ? 'var(--sa-negative)' : 'var(--sa-negative-bg)',
            color: state?.kill_switch ? 'var(--sa-bg)' : 'var(--sa-negative-soft)',
            fontSize: 'var(--sa-text-base)',
            fontWeight: 600,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9" />
            <line x1="12" y1="7" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12" y2="17" />
          </svg>
          <span>{state?.kill_switch ? 'Trading halted' : 'Kill switch'}</span>
        </button>
      </div>
    </header>
  );
}
