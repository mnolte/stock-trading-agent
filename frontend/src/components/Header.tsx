import { useEffect, useState } from 'react';

import type { HeaderState } from '../types/api';
import { displayTime, nyTime } from '../format';
import { Chip } from './Chip';
import { regimeVariant } from './regimeChip';
import { sessionLabel, sessionTail } from './sessionText';

/** "+42.10" → "+$42.10"; falls back to '—' when there is no figure. */
function signedMoney(value: string | null, symbol: '$' | '€'): string {
  if (!value) return '—';
  const sign = value.startsWith('-') ? '-' : '+';
  return `${sign}${symbol}${value.replace(/^[-+]/, '')}`;
}

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
  onPauseToggle,
  onMenu,
}: {
  state: HeaderState | null;
  title: string;
  onKillSwitch: () => void;
  onPauseToggle: () => void;
  onMenu?: () => void;
}) {
  const [, forceTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(timer);
  }, []);

  const sessionState = state?.session.state ?? 'closed';
  const paused = state?.paused ?? false;
  const pillState = paused ? 'paused' : sessionState;
  const tail = state ? sessionTail(pillState, state.session) : '';
  const sessionTitle = state
    ? [
        `New York ${nyTime(state.session.now)}`,
        state.session.next_close
          ? `market closes ${nyTime(state.session.next_close)} New York (${displayTime(state.session.next_close)} Amsterdam)`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined;
  const todayNum = state?.today_usd ? Number(state.today_usd.replace(/,/g, '')) : 0;
  const todayColor = !state
    ? 'var(--sa-text)'
    : todayNum > 0
      ? 'var(--sa-positive)'
      : todayNum < 0
        ? 'var(--sa-negative)'
        : 'var(--sa-text-muted)';

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
          title={sessionTitle ?? 'Market session state'}
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
              background: paused ? 'var(--sa-accent)' : sessionDot(sessionState),
            }}
          />
          <span>
            {sessionLabel(pillState)}
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
            ${state?.equity_usd ?? '—'}{' '}
            <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
              {state ? (state.equity_eur ? `€${state.equity_eur}` : 'EUR unavailable') : '—'}
            </span>
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
              color: todayColor,
              whiteSpace: 'nowrap',
            }}
          >
            {state ? signedMoney(state.today_usd, '$') : '—'}{' '}
            <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
              {state ? (state.today_eur ? signedMoney(state.today_eur, '€') : 'EUR unavailable') : '—'}
            </span>
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
          <Chip variant={regimeVariant(state?.regime)}>{state?.regime ?? '—'}</Chip>
        </div>

        <button
          type="button"
          onClick={onPauseToggle}
          disabled={paused && !!state?.resume_blocked}
          title={
            paused && state?.resume_blocked
              ? `Resume needs all three keys set and passing — ${state.keys_issue ?? 'open Settings'}`
              : paused
                ? undefined
                : 'Pause new entries. Stops, targets and position checks keep working.'
          }
          style={
            paused
              ? {
                  height: 'var(--sa-control-height)',
                  padding: '0 var(--sa-space-4)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 'var(--sa-space-3)',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-accent)',
                  background: 'var(--sa-accent)',
                  color: 'var(--sa-bg)',
                  fontSize: 'var(--sa-text-base)',
                  fontWeight: 600,
                  cursor: state?.resume_blocked ? 'not-allowed' : 'pointer',
                  opacity: state?.resume_blocked ? 0.55 : 1,
                  whiteSpace: 'nowrap',
                }
              : {
                  height: 'var(--sa-control-height)',
                  padding: '0 var(--sa-space-4)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 'var(--sa-space-3)',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-raised)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-base)',
                  fontWeight: 500,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                }
          }
        >
          {paused ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="7 4 20 12 7 20 7 4" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="8" y1="5" x2="8" y2="19" />
              <line x1="16" y1="5" x2="16" y2="19" />
            </svg>
          )}
          <span>{paused ? 'Resume' : 'Pause'}</span>
        </button>

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
