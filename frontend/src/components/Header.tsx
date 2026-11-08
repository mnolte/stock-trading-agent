import { useEffect, useRef, useState } from 'react';

import type { HeaderState } from '../types/api';
import { displayTime, nyTime } from '../format';
import { Chip } from './Chip';
import { NeedsChip } from './NeedsChip';
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

const statLabel: React.CSSProperties = {
  fontSize: 'var(--sa-text-xs)',
  color: 'var(--sa-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 'var(--sa-label-tracking)',
};

const statValue: React.CSSProperties = {
  fontFamily: 'var(--sa-font-mono)',
  fontSize: 'var(--sa-text-base)',
  whiteSpace: 'nowrap',
};

/** A shimmer bar shaped like the text it stands in for (055 → Loading). */
function Bar({ width, height = 14 }: { width: number | string; height?: number }) {
  return (
    <span
      className="sa-skel sa-loading"
      aria-hidden="true"
      style={{ display: 'inline-block', width, height, borderRadius: 4, verticalAlign: 'middle' }}
    />
  );
}

export function Header({
  state,
  title,
  onKillSwitch,
  onPauseToggle,
  killBusy = false,
  killBusyLabel = 'Turning on…',
  needsYou = 0,
  narrow = false,
}: {
  state: HeaderState | null;
  title: string;
  onKillSwitch: () => void;
  onPauseToggle: () => void;
  /** A kill-switch request is unanswered: the button shows the spinner
   * and the present-participle label, never disabled (055 → Loading). */
  killBusy?: boolean;
  killBusyLabel?: string;
  /** Needs-you count for the header chip (0 hides it; the phone bar
   * carries the count at ≤820 px, so the chip is not rendered there). */
  needsYou?: number;
  /** ≤820 px: the title row is hidden (the phone bar carries it), the
   * stats are one mono line and Pause / Kill switch split a row. */
  narrow?: boolean;
}) {
  // Server-clock skew measured on each fresh header payload, so countdowns
  // ("next cycle · in 15 min") tick between refetches instead of staying
  // frozen at the last fetch's server time (same pattern as CycleCounter).
  const [nowMs, setNowMs] = useState(() => Date.now());
  const skew = useRef(0);
  useEffect(() => {
    if (state?.session.now) {
      skew.current = Date.parse(state.session.now) - Date.now();
    }
  }, [state?.session.now]);
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now() + skew.current), 15_000);
    return () => clearInterval(timer);
  }, []);

  const sessionState = state?.session.state ?? 'closed';
  const paused = state?.paused ?? false;
  const loading = state === null;
  const pillState = paused ? 'paused' : sessionState;
  const estNow = new Date(nowMs).toISOString();
  const tail = state ? sessionTail(pillState, state.session, estNow) : '';
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

  const pauseButton = (
    <button
      type="button"
      onClick={loading ? undefined : onPauseToggle}
      disabled={!loading && paused && !!state?.resume_blocked}
      aria-disabled={loading || undefined}
      title={
        loading
          ? "Waiting for the agent's state"
          : paused && state?.resume_blocked
            ? `Resume needs all three keys set and passing — ${state.keys_issue ?? 'open Settings'}`
            : paused
              ? undefined
              : 'Pause new entries. Stops, targets and position checks keep working.'
      }
      style={{
        height: 'var(--sa-control-height)',
        padding: '0 var(--sa-space-4)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--sa-space-3)',
        borderRadius: 'var(--sa-radius-md)',
        border: paused ? '1px solid var(--sa-accent)' : '1px solid var(--sa-border-strong)',
        background: paused ? 'var(--sa-accent)' : 'var(--sa-raised)',
        color: loading
          ? 'var(--sa-text-disabled)'
          : paused
            ? 'var(--sa-bg)'
            : 'var(--sa-text)',
        fontSize: 'var(--sa-text-base)',
        fontWeight: paused ? 600 : 500,
        cursor:
          loading || (paused && state?.resume_blocked)
            ? 'not-allowed'
            : 'pointer',
        opacity: paused && state?.resume_blocked ? 0.55 : 1,
        whiteSpace: 'nowrap',
      }}
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
  );

  const killButton = (
    <button
      type="button"
      onClick={onKillSwitch}
      title={state?.kill_switch ? 'Resume trading' : 'Stop new entries, or stop and sell everything'}
      style={{
        height: 'var(--sa-control-height)',
        padding: '0 var(--sa-space-4)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--sa-space-3)',
        borderRadius: 'var(--sa-radius-md)',
        border: '1px solid var(--sa-negative-border)',
        background: state?.kill_switch ? 'var(--sa-negative)' : 'var(--sa-negative-bg)',
        color: killBusy
          ? 'var(--sa-negative)'
          : state?.kill_switch
            ? 'var(--sa-bg)'
            : 'var(--sa-negative-soft)',
        fontSize: 'var(--sa-text-base)',
        fontWeight: 600,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {killBusy ? (
        <span className="sa-spin" aria-hidden="true" />
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="7" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12" y2="17" />
        </svg>
      )}
      <span>{killBusy ? killBusyLabel : state?.kill_switch ? 'Trading halted' : 'Kill switch'}</span>
    </button>
  );

  return (
    <header
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        padding: narrow ? '10px 16px 12px' : '12px var(--sa-space-6) 14px',
        borderBottom: '1px solid var(--sa-border)',
        background: 'var(--sa-chrome)',
      }}
    >
      {!narrow && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)', minWidth: 0 }}>
          <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600, whiteSpace: 'nowrap' }}>
            {title}
          </h1>
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
              flex: '0 0 auto',
            }}
          >
            PAPER
          </span>
          <span
            style={{
              fontSize: 'var(--sa-text-md)',
              color: 'var(--sa-text-muted)',
              minWidth: 0,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {loading ? <Bar width={220} /> : state.session.display_time}
          </span>
        </div>
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 'var(--sa-space-3) var(--sa-space-4)',
          minWidth: 0,
        }}
      >
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
            minWidth: 0,
            overflow: 'hidden',
          }}
        >
          {loading ? (
            <Bar width={150} />
          ) : (
            <>
              <span
                style={{
                  width: 8,
                  height: 8,
                  flex: '0 0 8px',
                  borderRadius: '50%',
                  background: paused ? 'var(--sa-accent)' : sessionDot(sessionState),
                }}
              />
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {sessionLabel(pillState)}
                <span style={{ color: 'var(--sa-text-muted)' }}>{tail}</span>
              </span>
            </>
          )}
        </div>
        {!narrow && <NeedsChip count={needsYou} />}

        {!narrow && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--sa-space-5)',
              marginLeft: 'auto',
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
              <span style={statLabel}>Equity</span>
              <span style={{ ...statValue, color: 'var(--sa-text)' }}>
                {loading ? (
                  <Bar width={120} />
                ) : (
                  <>
                    ${state.equity_usd}{' '}
                    <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                      {state.equity_eur ? `€${state.equity_eur}` : 'EUR unavailable'}
                    </span>
                  </>
                )}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
              <span style={statLabel}>Today</span>
              <span style={{ ...statValue, color: todayColor }}>
                {loading ? (
                  <Bar width={90} />
                ) : (
                  <>
                    {signedMoney(state.today_usd, '$')}{' '}
                    <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                      {state.today_eur ? signedMoney(state.today_eur, '€') : 'EUR unavailable'}
                    </span>
                  </>
                )}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              <span
                title="Market regime from SPY: trend × volatility"
                style={{
                  ...statLabel,
                  textDecoration: 'underline dotted',
                  textUnderlineOffset: '3px',
                  cursor: 'help',
                  alignSelf: 'flex-start',
                }}
              >
                Regime
              </span>
              {loading ? <Bar width={72} height={22} /> : <Chip variant={regimeVariant(state.regime)}>{state.regime}</Chip>}
            </div>
          </div>
        )}

        {narrow && (
          <div
            style={{
              flex: '1 1 100%',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--sa-space-3)',
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-md)',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {loading ? (
              <>
                <Bar width={90} />
                <Bar width={80} />
                <Bar width={72} height={22} />
              </>
            ) : (
              <>
                <span
                  title={`Equity · ${state.equity_eur ? `€${state.equity_eur}` : 'EUR unavailable'}`}
                  style={{ color: 'var(--sa-text)' }}
                >
                  ${state.equity_usd}
                </span>
                <span
                  title={`Today · ${state.today_eur ? signedMoney(state.today_eur, '€') : 'EUR unavailable'}`}
                  style={{ color: todayColor }}
                >
                  {signedMoney(state.today_usd, '$')}
                </span>
                <Chip variant={regimeVariant(state.regime)}>{state.regime}</Chip>
              </>
            )}
          </div>
        )}

        <div
          style={
            narrow
              ? {
                  flex: '1 1 100%',
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 'var(--sa-space-3)',
                }
              : { display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)' }
          }
        >
          {pauseButton}
          {killButton}
        </div>
      </div>
    </header>
  );
}
