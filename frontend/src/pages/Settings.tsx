import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import {
  getKeys,
  getNotifications,
  getSettings,
  getUniverse,
  postKey,
  postKeyTest,
  postNotificationTest,
  postPaperReset,
  postSettings,
  postSmtpSettings,
  postStartRun,
} from '../api/state';
import type {
  KeyGroup,
  KeyRow,
  KeysResponse,
  NotificationEventMeta,
  NotificationItem,
  SettingsForm,
  SettingsResponse,
  SmtpStatus,
  UniverseResponse,
} from '../types/api';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { ResetAccountDialog } from '../components/SettingsDialogs';
import { displayDateTime, displayDayTime, displayTime } from '../format';
import { diffMap, diffSettings, fmtPath } from './settingsDiff';
import { validateForm } from './settingsValidation';

// ---------------------------------------------------------------------------
// design tokens (design/screens/Settings.dc.html)

const inputBox = (accent = false, invalid = false): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  height: 40,
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: `1px solid ${invalid ? 'var(--sa-negative-text)' : accent ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
  background: 'var(--sa-bg)',
});

const numInput = (width = 72): CSSProperties => ({
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
  outline: 'none',
});

const unitText: CSSProperties = {
  padding: '0 10px 0 2px',
  fontSize: 'var(--sa-text-md)',
  color: 'var(--sa-text-muted)',
  whiteSpace: 'nowrap',
};

const textInput = (flex?: string): CSSProperties => ({
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

const selectStyle: CSSProperties = {
  ...textInput(),
  minWidth: 200,
};

const rowFrame = (changed: boolean): CSSProperties => ({
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

const subHead: CSSProperties = {
  margin: '18px 0 0',
  fontSize: 'var(--sa-text-sm)',
  fontWeight: 600,
  color: 'var(--sa-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
};

const chip = (tone: 'ok' | 'bad' | 'neutral'): CSSProperties => ({
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

function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 12px' }}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

// spec defaults (backend/config.example.yaml) shown under each control
const DEFAULTS: Record<string, string> = {
  'execution.mode': 'Automatic',
  'execution.entry_limit_offset_pct': '0.05%',
  'execution.entry_timeout_minutes': '10 min',
  'execution.max_spread_pct': '0.15%',
  'decision.model': 'gpt-4o-mini',
  'decision.temperature': '0',
  'decision.min_confidence_enter': '0.55',
  'decision.min_reward_risk': '1.5',
  'decision.max_calls_per_cycle': '10',
  'risk.risk_per_trade_pct': '0.5%',
  'risk.max_position_pct': '20%',
  'risk.max_order_pct': '25%',
  'risk.max_open_risk_pct': '2.5%',
  'risk.max_open_positions': '4',
  'risk.max_per_sector': '2',
  'risk.daily_loss_limit_pct': '2%',
  'risk.losing_streak': '3',
  'risk.drawdown_kill_pct': '8%',
  'risk.max_orders_per_cycle': '6',
  'risk.earnings_blackout_days': '3',
  'risk.hold_through_earnings': 'Off',
  'performance.slippage_pct_per_side': '0.05%',
  'reviews.briefing_enabled': 'On',
  'reviews.monthly_cost_cap_usd': '$2',
  'universe.mode': 'Fixed list',
  'universe.screen.min_price': '$10',
  'universe.screen.min_avg_dollar_volume': '$100M',
  'universe.screen.lookback_sessions': '20',
  'universe.screen.drop_after_failed_screens': '3',
  'notifications.repeat_limit_minutes': '30 min',
};

// field path -> unit suffix for "Changed from …" and the unsaved list
const UNITS: Record<string, string> = {
  'execution.entry_limit_offset_pct': '%',
  'execution.entry_timeout_minutes': ' min',
  'execution.max_spread_pct': '%',
  'risk.risk_per_trade_pct': '%',
  'risk.max_position_pct': '%',
  'risk.max_order_pct': '%',
  'risk.max_open_risk_pct': '%',
  'risk.daily_loss_limit_pct': '%',
  'risk.shock_brake_1h_pct': '%',
  'risk.shock_brake_session_pct': '%',
  'risk.drawdown_kill_pct': '%',
  'risk.losing_streak': ' trades',
  'risk.earnings_blackout_days': ' trading days',
  'performance.slippage_pct_per_side': '%',
  'reviews.monthly_cost_cap_usd': '',
  'notifications.repeat_limit_minutes': ' min',
};

// field path -> section id, for the section-list change badges
const SECTION_OF: Record<string, string> = {
  'execution.mode': 'trading',
  'performance.slippage_pct_per_side': 'trading',
  'risk.risk_per_trade_pct': 'risk',
  'risk.max_position_pct': 'risk',
  'risk.max_order_pct': 'risk',
  'risk.max_open_risk_pct': 'risk',
  'risk.max_open_positions': 'risk',
  'risk.max_per_sector': 'risk',
  'risk.daily_loss_limit_pct': 'risk',
  'risk.losing_streak': 'risk',
  'risk.shock_brake_1h_pct': 'risk',
  'risk.shock_brake_session_pct': 'risk',
  'risk.drawdown_kill_pct': 'risk',
  'risk.max_orders_per_cycle': 'risk',
  'rules.rsi_min': 'rules',
  'rules.rsi_max': 'rules',
  'decision.min_confidence_enter': 'rules',
  'decision.stop_atr_min': 'rules',
  'decision.stop_atr_max': 'rules',
  'decision.min_reward_risk': 'rules',
  'execution.max_spread_pct': 'rules',
  'execution.entry_limit_offset_pct': 'rules',
  'execution.entry_timeout_minutes': 'rules',
  'risk.earnings_blackout_days': 'earnings',
  'risk.hold_through_earnings': 'earnings',
  'decision.model': 'model',
  'decision.prompt': 'model',
  'decision.temperature': 'model',
  'decision.max_calls_per_cycle': 'model',
  'reviews.briefing_enabled': 'model',
  'reviews.monthly_cost_cap_usd': 'model',
};

const SECTIONS = [
  { id: 'keys', label: 'API keys and data' },
  { id: 'trading', label: 'Trading' },
  { id: 'risk', label: 'Risk limits' },
  { id: 'rules', label: 'Entry rules' },
  { id: 'earnings', label: 'Earnings' },
  { id: 'model', label: 'Model and reviews' },
  { id: 'notify', label: 'Notifications' },
  { id: 'schedule', label: 'Schedule' },
  { id: 'history', label: 'History' },
  { id: 'danger', label: 'Paper account' },
] as const;

const SEARCH_TEXT: Record<string, string> = {
  keys: 'api keys alpaca openai finnhub key test replace data sources market data news earnings ecb connections',
  trading:
    'trading execution mode automatic supervised universe fixed list screened pool liquidity screen min price average dollar volume drop after one share fits slippage assumed',
  risk:
    'risk limits per trade max position size single order total open risk positions sector cash only daily loss limit losing streak shock brake drawdown kill orders per cycle brakes portfolio',
  rules:
    'entry rules checks rsi range confidence stop distance reward risk atr spread entry limit cancel unfilled orders model answer',
  earnings: 'earnings blackout hold through report sell trading days',
  model:
    'model reviews decisions prompt temperature calls per cycle daily briefing review cost cap',
  notify:
    'notifications alerts browser email smtp quiet hours repeat limit critical warnings trading reviews send test channels',
  schedule: 'schedule cycles new york amsterdam bar used entries position checks times',
  history: 'history version reason change settings log',
  danger: 'paper account reset new run start over balance',
};

// ---------------------------------------------------------------------------
// small building blocks

function Section({
  id,
  title,
  right,
  blurb,
  children,
}: {
  id: string;
  title: string;
  right?: ReactNode;
  blurb?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      data-settings-section
      style={{
        scrollMarginTop: 24,
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: '18px 20px 6px',
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>{title}</h2>
        {right && <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>{right}</div>}
      </div>
      {blurb && (
        <p style={{ margin: '4px 0 6px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', lineHeight: 1.5, maxWidth: 640 }}>
          {blurb}
        </p>
      )}
      {children}
    </section>
  );
}

function Row({
  id,
  label,
  help,
  changed,
  changedFrom,
  error,
  locked,
  defaultText,
  pendingText,
  onReset,
  children,
}: {
  id?: string;
  label: string;
  help: ReactNode;
  changed?: boolean;
  changedFrom?: string;
  error?: string;
  locked?: boolean;
  defaultText?: string;
  pendingText?: string;
  onReset?: () => void;
  children: ReactNode;
}) {
  return (
    <div style={rowFrame(!!changed)}>
      <div style={{ flex: '1 1 300px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <label htmlFor={id} style={{ fontSize: 'var(--sa-text-base)', color: 'var(--sa-text)' }}>
          {label}
        </label>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.45 }}>{help}</span>
      </div>
      <div style={{ flex: '0 0 auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
        {children}
        {error ? (
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-negative-text)' }}>{error}</span>
        ) : locked ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            <LockIcon /> fixed by the spec
          </span>
        ) : changed ? (
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-accent)' }}>
            Changed from {changedFrom} ·{' '}
            <button
              type="button"
              onClick={onReset}
              style={{ background: 'none', border: 0, color: 'var(--sa-accent)', cursor: 'pointer', fontSize: 'var(--sa-text-sm)', padding: 0 }}
            >
              Reset
            </button>
          </span>
        ) : (
          defaultText !== undefined && (
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-faint)' }}>Default {defaultText}</span>
          )
        )}
        {pendingText !== undefined && (
          <span
            title="Saved in the pending version — applies at the next cycle"
            style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-faint)' }}
          >
            Pending: {pendingText}
          </span>
        )}
      </div>
    </div>
  );
}

function NumBox({
  id,
  label,
  value,
  unit,
  accent,
  invalid,
  width,
  onChange,
}: {
  id?: string;
  label?: string;
  value: number;
  unit?: string;
  accent?: boolean;
  invalid?: boolean;
  width?: number;
  onChange: (value: number) => void;
}) {
  return (
    <span style={inputBox(accent, invalid)}>
      <input
        id={id}
        aria-label={label}
        value={Number.isNaN(value) ? '' : value}
        inputMode="decimal"
        onChange={(e) => onChange(Number(e.target.value))}
        style={numInput(width)}
      />
      {unit && <span style={unitText}>{unit}</span>}
    </span>
  );
}

function Switch({
  id,
  on,
  locked,
  onChange,
}: {
  id?: string;
  on: boolean;
  locked?: boolean;
  onChange?: (on: boolean) => void;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={on}
      disabled={locked}
      onClick={() => onChange?.(!on)}
      style={{
        position: 'relative',
        width: 44,
        height: 26,
        borderRadius: 999,
        border: 0,
        background: on ? 'var(--sa-accent)' : 'var(--sa-border-strong)',
        cursor: locked ? 'not-allowed' : 'pointer',
        opacity: locked ? 0.55 : 1,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 3,
          ...(on ? { right: 3 } : { left: 3 }),
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: 'var(--sa-text)',
        }}
      />
    </button>
  );
}

/** Warn on tab close and on in-app links while there are unsaved changes. */
function useUnsavedGuard(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    const onClick = (e: MouseEvent) => {
      const anchor = (e.target as HTMLElement | null)?.closest?.('a[href]');
      if (!anchor) return;
      const href = anchor.getAttribute('href') ?? '';
      if (href.startsWith('#') || !href.startsWith('/')) return;
      if (!window.confirm('You have unsaved changes — leave and discard them?')) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [active]);
}

// ---------------------------------------------------------------------------
// API keys

const KEY_FIELD_LABELS: Record<KeyGroup, string> = {
  alpaca: 'New Alpaca key id',
  openai: 'New OpenAI API key',
  finnhub: 'New Finnhub API key',
};

const KEY_FORM_NOTES: Record<KeyGroup, string> = {
  alpaca:
    'The keys are tested with a paper-account check before they are saved. The old keys keep working until the new ones pass. Only paper keys are accepted.',
  openai:
    'The key is tested with a models list before it is saved. The old key keeps working until the new one passes.',
  finnhub:
    'The key is tested with one earnings-calendar request before it is saved. The old key keeps working until the new one passes.',
};

function keyStatusChip(row: KeyRow): { text: string; tone: 'ok' | 'bad' | 'neutral' } {
  if (row.status === 'ok')
    return { text: row.name === 'alpaca' ? 'connected · paper' : 'ok', tone: 'ok' };
  if (row.status === 'failing') return { text: 'failing', tone: 'bad' };
  if (row.status === 'missing') return { text: 'not set', tone: 'bad' };
  return { text: 'saved · untested', tone: 'neutral' };
}

function KeysSection({
  keys,
  onChanged,
  onNotice,
  onError,
}: {
  keys: KeysResponse;
  onChanged: () => void;
  onNotice: (text: string) => void;
  onError: (messages: string[]) => void;
}) {
  const [openForm, setOpenForm] = useState<KeyGroup | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [keyId, setKeyId] = useState('');
  const [secret, setSecret] = useState('');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState<KeyGroup | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const [testNote, setTestNote] = useState<Record<string, string>>({});

  const openReplace = (group: KeyGroup) => {
    setOpenForm(openForm === group ? null : group);
    setKeyId('');
    setSecret('');
    setValue('');
    setShowKey(false);
    setRowError({});
  };

  const testStored = (group: KeyGroup) => {
    setBusy(group);
    postKeyTest(group)
      .then((res) => {
        setTestNote((n) => ({ ...n, [group]: res.ok ? 'tested ok just now' : `failed: ${res.error ?? 'unknown'}` }));
        onChanged();
      })
      .catch((e) => setRowError((r) => ({ ...r, [group]: errorMessages(e).join(' ') })))
      .finally(() => setBusy(null));
  };

  const saveKey = (group: KeyGroup) => {
    setBusy(group);
    const body = group === 'alpaca' ? { key_id: keyId, secret } : { value };
    postKey(group, body)
      .then((res) => {
        setOpenForm(null);
        setKeyId('');
        setSecret('');
        setValue('');
        onNotice(
          res.account_changed
            ? 'Alpaca keys saved — they point at a different paper account. Start a new run to continue.'
            : `${res.key.label} saved — tested ok.`,
        );
        onChanged();
      })
      .catch((e) => {
        setRowError((r) => ({ ...r, [group]: errorMessages(e).join(' ') }));
        onError([]);
      })
      .finally(() => setBusy(null));
  };

  const keyField = (
    id: string,
    label: string,
    fieldValue: string,
    set: (v: string) => void,
    showToggle: boolean,
  ) => (
    <>
      <label htmlFor={id} style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
        {label}
      </label>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <span style={{ ...inputBox(true), flex: '1 1 280px', borderColor: 'var(--sa-accent)' }}>
          <input
            id={id}
            type={showToggle && showKey ? 'text' : 'password'}
            value={fieldValue}
            onChange={(e) => set(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            style={{ ...numInput(), flex: 1, minWidth: 0, width: 'auto', textAlign: 'left' }}
          />
          {showToggle && (
            <button
              type="button"
              aria-label={showKey ? 'Hide key' : 'Show key'}
              title={showKey ? 'Hide key' : 'Show key'}
              onClick={() => setShowKey(!showKey)}
              style={{ width: 40, height: 38, border: 0, background: 'transparent', color: 'var(--sa-text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>
          )}
        </span>
      </div>
    </>
  );

  return (
    <Section
      id="keys"
      title="API keys and data"
      right={`${keys.set_count} of 3 keys set`}
      blurb="Connections the agent needs. A missing or failing key keeps the agent paused and shows a banner."
    >
      <div
        style={{
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
          padding: '12px 14px',
          margin: '6px 0 4px',
          border: '1px solid var(--sa-border)',
          background: 'var(--sa-raised)',
          borderRadius: 'var(--sa-radius-md)',
          fontSize: 'var(--sa-text-md)',
          color: 'var(--sa-text-2)',
          lineHeight: 1.5,
        }}
      >
        <span style={{ color: 'var(--sa-text-muted)', marginTop: 2 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
            <polyline points="9 12 11 14 15 10" />
          </svg>
        </span>
        <span>
          Keys are stored encrypted on this computer and are never shown again in full. They are
          saved on their own, straight away, and are not part of settings versions.
        </span>
      </div>

      {keys.env_keys && (
        <div
          style={{
            display: 'flex',
            gap: 10,
            alignItems: 'flex-start',
            padding: '12px 14px',
            margin: '0 0 4px',
            border: '1px solid var(--sa-accent-border)',
            background: 'var(--sa-accent-bg)',
            borderRadius: 'var(--sa-radius-md)',
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
            lineHeight: 1.5,
          }}
        >
          Keys found in .env were imported once and are now ignored — remove them from .env.
        </div>
      )}

      {keys.keys.map((row) => {
        const status = keyStatusChip(row);
        const isAlpaca = row.name === 'alpaca';
        const replaceDisabled = isAlpaca && !keys.replace_allowed;
        return (
          <div key={row.name} style={{ padding: '14px 0', borderBottom: '1px solid var(--sa-border)', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px 20px' }}>
              <div style={{ flex: '1 1 260px', minWidth: 0, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <span style={{ flex: '0 0 32px', height: 32, borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sa-text-2)' }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="8" cy="15" r="4" />
                    <line x1="11" y1="12" x2="20" y2="3" />
                    <line x1="16" y1="7" x2="19" y2="10" />
                  </svg>
                </span>
                <span style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                  <span style={{ fontSize: 'var(--sa-text-base)', fontWeight: 600 }}>{row.label}</span>
                  <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{row.hint}</span>
                </span>
              </div>
              <div style={{ flex: '0 1 300px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={chip(status.tone)}>{status.text}</span>
                  {row.masked && (
                    <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                      {row.masked}
                    </span>
                  )}
                </span>
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  {row.account && `Account ${row.account} · `}
                  {row.tested_at ? `tested ${displayTime(row.tested_at)}` : 'not tested yet'}
                  {row.saved_at ? ` · saved ${displayDayTime(row.saved_at)}` : ''}
                  {row.error ? ` · ${row.error}` : ''}
                  {testNote[row.name] ? ` · ${testNote[row.name]}` : ''}
                </span>
              </div>
              <div style={{ flex: '0 0 auto', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                {row.set && (
                  <Button variant="ghost" onClick={() => testStored(row.name)} disabled={busy !== null} style={{ height: 36, padding: '0 16px', border: '1px solid var(--sa-border)' }}>
                    {busy === row.name ? 'Testing…' : 'Test'}
                  </Button>
                )}
                {isAlpaca ? (
                  <Button
                    onClick={() => openReplace(row.name)}
                    disabled={replaceDisabled || busy !== null}
                    title={replaceDisabled ? keys.replace_hint : undefined}
                    style={{ height: 36, padding: '0 14px' }}
                  >
                    Replace
                  </Button>
                ) : (
                  <Button onClick={() => openReplace(row.name)} disabled={busy !== null} style={{ height: 36, padding: '0 14px' }}>
                    {row.set ? 'Replace' : 'Add key'}
                  </Button>
                )}
              </div>
            </div>

            {isAlpaca && row.set && (
              <div style={{ marginLeft: 44, display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                <LockIcon /> {keys.replace_hint}
              </div>
            )}

            {rowError[row.name] && (
              <div style={{ marginLeft: 44, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-negative-text)' }}>
                {rowError[row.name]}
              </div>
            )}

            {openForm === row.name && (
              <div style={{ marginLeft: 0, padding: 14, border: '1px solid var(--sa-accent-border)', background: 'var(--sa-accent-bg)', borderRadius: 'var(--sa-radius-md)', display: 'flex', flexDirection: 'column', gap: 10 }}>
                {isAlpaca ? (
                  <>
                    {keyField('k-alpaca-id', 'New Alpaca key id', keyId, setKeyId, true)}
                    {keyField('k-alpaca-secret', 'New Alpaca secret', secret, setSecret, false)}
                  </>
                ) : (
                  keyField(`k-${row.name}`, KEY_FIELD_LABELS[row.name], value, setValue, true)
                )}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                  <Button variant="ghost" onClick={() => setOpenForm(null)} style={{ height: 40, padding: '0 16px', border: '1px solid var(--sa-border)' }}>
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    onClick={() => saveKey(row.name)}
                    disabled={busy !== null || (isAlpaca ? !keyId.trim() || !secret.trim() : !value.trim())}
                    style={{ height: 40, padding: '0 16px' }}
                  >
                    {busy === row.name ? 'Testing…' : 'Test and save'}
                  </Button>
                </div>
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{KEY_FORM_NOTES[row.name]}</span>
              </div>
            )}
          </div>
        );
      })}

      <h3 style={subHead}>Data sources</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8, padding: '10px 0 14px' }}>
        {keys.data_sources.map((source) => (
          <div key={source.source} style={{ padding: '10px 12px', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 'var(--sa-text-md)' }}>
              <b style={{ fontWeight: 600 }}>{source.source}</b>
              <span style={chip(source.status === 'ok' || source.status === 'connected' ? 'ok' : source.status === 'simulated' ? 'neutral' : 'bad')}>
                {source.status}
              </span>
            </span>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{source.detail}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// notifications section (design/screens/Settings.dc.html → #notify)

const NOTIFY_GROUPS: [string, string][] = [
  ['critical', 'Critical · always sent, never held back'],
  ['warnings', 'Warnings'],
  ['trading', 'Trading'],
  ['reviews', 'Reviews'],
];

function ChannelCheck({
  checked,
  locked,
  label,
  onToggle,
}: {
  checked: boolean;
  locked?: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      aria-disabled={locked || undefined}
      title={locked ? 'Always on for critical alerts' : undefined}
      onClick={locked ? undefined : onToggle}
      style={{
        width: 22,
        height: 22,
        borderRadius: 5,
        border: `1px solid ${checked ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
        background: checked ? 'var(--sa-accent)' : 'transparent',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 0,
        opacity: locked ? 0.55 : 1,
        cursor: locked ? 'not-allowed' : 'pointer',
      }}
    >
      {checked && (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--sa-bg)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="5 12 10 17 19 7" />
        </svg>
      )}
    </button>
  );
}

/** The event × channel matrix. In-app banners are always on and not a
 * column; critical rows lock the browser checkbox on (spec). */
function EventChannelTable({
  events,
  channels,
  changed,
  onToggle,
  onReset,
}: {
  events: NotificationEventMeta[];
  channels: Record<string, string[]>;
  changed: Record<string, string>;
  onToggle: (key: string, channel: 'browser' | 'email') => void;
  onReset: (key: string) => void;
}) {
  const cell: CSSProperties = {
    padding: '9px 6px',
    borderBottom: '1px solid var(--sa-border)',
    verticalAlign: 'middle',
  };
  return (
    <div style={{ flex: '3 1 420px', minWidth: 0 }}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th scope="col" style={{ textAlign: 'left', width: 'auto', padding: '8px 6px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)' }}>Event</th>
              <th scope="col" style={{ textAlign: 'center', width: 60, padding: '8px 6px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)' }}>Browser</th>
              <th scope="col" style={{ textAlign: 'center', width: 60, padding: '8px 6px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)' }}>Email</th>
            </tr>
          </thead>
          <tbody>
            {NOTIFY_GROUPS.map(([group, heading]) => {
              const rows = events.filter((ev) => ev.group === group);
              if (rows.length === 0) return null;
              return [
                <tr key={`g-${group}`}>
                  <td colSpan={3} style={{ padding: '14px 6px 6px', fontSize: 'var(--sa-text-sm)', fontWeight: 600, color: 'var(--sa-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                    {heading}
                  </td>
                </tr>,
                ...rows.map((ev) => {
                  const chans = channels[ev.key] ?? [];
                  const critical = ev.severity === 'critical';
                  const rowChanged = `notifications.events.${ev.key}` in changed;
                  return (
                    <tr key={ev.key}>
                      <td style={{ ...cell, fontSize: 'var(--sa-text-md)' }}>
                        {ev.label}
                        {ev.description && (
                          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 2 }}>
                            {ev.description}
                          </div>
                        )}
                        {rowChanged && (
                          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-accent)', marginTop: 2 }}>
                            Changed from {changed[`notifications.events.${ev.key}`]} ·{' '}
                            <button
                              type="button"
                              onClick={() => onReset(ev.key)}
                              style={{ background: 'none', border: 0, color: 'var(--sa-accent)', cursor: 'pointer', fontSize: 'var(--sa-text-sm)', padding: 0, textDecoration: 'underline' }}
                            >
                              Reset
                            </button>
                          </div>
                        )}
                      </td>
                      <td style={{ ...cell, textAlign: 'center' }}>
                        <ChannelCheck
                          checked={critical || chans.includes('browser')}
                          locked={critical}
                          label={`${ev.label}, browser`}
                          onToggle={() => onToggle(ev.key, 'browser')}
                        />
                      </td>
                      <td style={{ ...cell, textAlign: 'center' }}>
                        <ChannelCheck
                          checked={chans.includes('email')}
                          label={`${ev.label}, email`}
                          onToggle={() => onToggle(ev.key, 'email')}
                        />
                      </td>
                    </tr>
                  );
                }),
              ];
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The Browser card: Web Notifications API permission state plus the
 * one-click request. Browser permission lives in the browser, not in
 * settings versions. */
function BrowserNotifyCard() {
  const [perm, setPerm] = useState<NotificationPermission | 'unsupported'>(() =>
    typeof window !== 'undefined' && 'Notification' in window
      ? Notification.permission
      : 'unsupported',
  );
  const status =
    perm === 'granted'
      ? { tone: 'ok' as const, text: 'allowed' }
      : perm === 'denied'
        ? { tone: 'bad' as const, text: 'blocked' }
        : perm === 'unsupported'
          ? { tone: 'neutral' as const, text: 'unavailable' }
          : { tone: 'neutral' as const, text: 'not allowed yet' };
  return (
    <div style={{ padding: 14, border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <b style={{ fontWeight: 600, fontSize: 'var(--sa-text-base)' }}>Browser</b>
        <span style={chip(status.tone)}>{status.text}</span>
      </div>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
        Works while Stock Agent is open in a browser tab. Also shown as a toast in the app.
        {perm === 'denied' && ' Blocked in the browser — allow notifications for this page in its site settings.'}
      </span>
      {perm === 'default' && (
        <div>
          <Button
            variant="ghost"
            onClick={() => {
              Notification.requestPermission()
                .then((p) => setPerm(p))
                .catch(() => setPerm('unsupported'));
            }}
            style={{ height: 36, padding: '0 16px', border: '1px solid var(--sa-border)' }}
          >
            Allow notifications
          </Button>
        </div>
      )}
    </div>
  );
}

interface SmtpFormState {
  host: string;
  port: string;
  username: string;
  password: string;
  from_addr: string;
  to_addr: string;
}

/** The Email (SMTP) card. Saves only after a successful test send on the
 * backend; the password goes to the encrypted secrets store and is never
 * read back. */
function SmtpCard({
  smtp,
  onSaved,
  onNotice,
  onError,
}: {
  smtp: SmtpStatus | null;
  onSaved: () => void;
  onNotice: (text: string) => void;
  onError: (msgs: string[]) => void;
}) {
  const [form, setForm] = useState<SmtpFormState>({
    host: '',
    port: '587',
    username: '',
    password: '',
    from_addr: '',
    to_addr: '',
  });
  const [busy, setBusy] = useState<'save' | 'test' | null>(null);
  const hydrated = useRef(false);

  useEffect(() => {
    // Fill the saved values once; later edits must not be overwritten by
    // a background settings refresh.
    if (smtp && !hydrated.current) {
      hydrated.current = true;
      setForm({
        host: smtp.host ?? '',
        port: String(smtp.port ?? 587),
        username: smtp.username ?? '',
        password: '',
        from_addr: smtp.from_addr ?? '',
        to_addr: smtp.to_addr ?? '',
      });
    }
  }, [smtp]);

  const field = (
    id: string,
    label: string,
    value: string,
    set: (v: string) => void,
    opts: { type?: string; placeholder?: string; mono?: boolean } = {},
  ) => (
    <label htmlFor={id} style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{label}</span>
      <input
        id={id}
        type={opts.type ?? 'text'}
        value={value}
        placeholder={opts.placeholder}
        onChange={(e) => set(e.target.value)}
        style={{ ...textInput(), width: '100%', fontFamily: opts.mono ? 'var(--sa-font-mono)' : undefined }}
      />
    </label>
  );

  const status = !smtp?.configured
    ? { tone: 'neutral' as const, text: 'not set up' }
    : smtp.test_result === 'ok' && smtp.tested_at
      ? { tone: 'ok' as const, text: `test sent ${displayTime(smtp.tested_at)}` }
      : smtp.test_result === 'failed'
        ? { tone: 'bad' as const, text: 'test failed' }
        : { tone: 'neutral' as const, text: 'saved' };

  const save = () => {
    const port = Number(form.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      onError(['Port: a whole number between 1 and 65535.']);
      return;
    }
    setBusy('save');
    postSmtpSettings({
      host: form.host.trim(),
      port,
      username: form.username.trim(),
      from_addr: form.from_addr.trim(),
      to_addr: form.to_addr.trim(),
      ...(form.password ? { password: form.password } : {}),
    })
      .then(() => {
        setForm((f) => ({ ...f, password: '' }));
        onNotice('SMTP saved — the test send passed.');
        onSaved();
      })
      .catch((e) => onError(errorMessages(e)))
      .finally(() => setBusy(null));
  };

  const sendTest = () => {
    setBusy('test');
    postNotificationTest()
      .then(() => onNotice('Test sent — see Recently sent below.'))
      .catch((e) => onError(errorMessages(e)))
      .finally(() => setBusy(null));
  };

  return (
    <div style={{ padding: 14, border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <b style={{ fontWeight: 600, fontSize: 'var(--sa-text-base)' }}>Email (SMTP)</b>
        <span style={chip(status.tone)}>{status.text}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10 }}>
        {field('smtp-host', 'SMTP host', form.host, (v) => setForm((f) => ({ ...f, host: v })), { mono: true })}
        <label htmlFor="smtp-port" style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Port · TLS</span>
          <span style={inputBox()}>
            <input
              id="smtp-port"
              value={form.port}
              inputMode="numeric"
              onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
              style={numInput(56)}
            />
            <span style={unitText}>STARTTLS</span>
          </span>
        </label>
        {field('smtp-user', 'Username', form.username, (v) => setForm((f) => ({ ...f, username: v })))}
        {field('smtp-pass', 'Password (encrypted)', form.password, (v) => setForm((f) => ({ ...f, password: v })), {
          type: 'password',
          placeholder: smtp?.password_set ? '••••••••' : undefined,
        })}
        {field('smtp-from', 'From', form.from_addr, (v) => setForm((f) => ({ ...f, from_addr: v })), { type: 'email' })}
        {field('smtp-to', 'To', form.to_addr, (v) => setForm((f) => ({ ...f, to_addr: v })), { type: 'email' })}
      </div>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
        The password is stored encrypted like the API keys and never shown again. Saving tests the connection first.
      </span>
      {smtp?.test_result === 'failed' && smtp.test_detail && (
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-negative-text)' }}>{smtp.test_detail}</span>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button
          variant="ghost"
          onClick={sendTest}
          disabled={busy !== null || !smtp?.configured}
          title={!smtp?.configured ? 'Save the SMTP details first' : undefined}
          style={{ height: 36, padding: '0 16px', border: '1px solid var(--sa-border-strong)', background: 'var(--sa-raised)' }}
        >
          {busy === 'test' ? 'Sending…' : 'Send test'}
        </Button>
        <Button
          variant="ghost"
          onClick={save}
          disabled={busy !== null || !form.host.trim() || !form.to_addr.trim()}
          style={{ height: 36, padding: '0 16px' }}
        >
          {busy === 'save' ? 'Testing…' : 'Test and save'}
        </Button>
      </div>
    </div>
  );
}

const NOTIFY_STATUS_TEXT: Record<string, string> = {
  sent: '✓',
  failed: '✗',
  queued: 'sending…',
  'held back': 'held back',
  'quiet hours': 'held',
};

function recentStatus(item: NotificationItem): string {
  const parts = Object.entries(item.channel_status)
    .filter(([channel]) => channel !== 'app')
    .map(([channel, st]) => `${channel} ${NOTIFY_STATUS_TEXT[st] ?? st}`);
  return parts.join(' · ') || 'in app';
}

/** The "Recently sent" rows — the `notifications` table, newest first. */
function RecentNotifications() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const refresh = useCallback(() => {
    getNotifications(0, 10)
      .then((res) => setItems(res.items))
      .catch(() => setItems([]));
  }, []);
  useEffect(refresh, [refresh]);
  useLiveUpdates(['notifications'], refresh);
  return (
    <div style={{ padding: '6px 0 14px' }}>
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {items.map((item) => (
          <div
            key={item.id}
            style={{ display: 'grid', gridTemplateColumns: '92px 1fr auto', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', alignItems: 'baseline' }}
          >
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {displayDayTime(item.time)}
            </span>
            <span style={{ color: 'var(--sa-text-2)' }}>
              {item.subject.replace(/^\[Stock Agent[^\]]*\]\s*/, '')}
            </span>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {recentStatus(item)}
            </span>
          </div>
        ))}
        {items.length === 0 && (
          <div style={{ padding: '8px 0', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            Nothing sent yet.
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// page

export default function Settings() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [keysData, setKeysData] = useState<KeysResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState('');
  const [find, setFind] = useState('');
  const [activeSection, setActiveSection] = useState('keys');
  const [newSymbol, setNewSymbol] = useState('');
  const [newSector, setNewSector] = useState('');
  const [resetOpen, setResetOpen] = useState(false);
  const [resetStep2, setResetStep2] = useState<string | null>(null);
  const [resetBusy, setResetBusy] = useState(false);

  const [universe, setUniverse] = useState<UniverseResponse | null>(null);

  const refresh = useCallback(() => {
    getSettings()
      .then((d) => {
        setData(d);
        setForm((f) => f ?? structuredClone(d.settings));
      })
      .catch(console.error);
    getUniverse().then(setUniverse).catch(() => setUniverse(null));
  }, []);

  const refreshKeys = useCallback(() => {
    getKeys().then(setKeysData).catch(console.error);
  }, []);

  useEffect(() => {
    refresh();
    refreshKeys();
  }, [refresh, refreshKeys]);
  useLiveUpdates(['header', 'universe'], refresh);
  useLiveUpdates(['settings', 'banners'], refreshKeys);

  const pendingMap = data?.pending ?? {};
  const pendingText = (path: string): string | undefined =>
    path in pendingMap ? fmtPath(path, pendingMap[path]) : undefined;
  const pendingJoined = (
    paths: string[],
    join: string,
    fmt?: (value: unknown) => string,
  ): string | undefined => {
    const vals = paths
      .filter((p) => p in pendingMap)
      .map((p) => (fmt ? fmt(pendingMap[p]) : String(pendingMap[p])));
    return vals.length ? vals.join(join) : undefined;
  };

  const diffs = useMemo(
    () => (data && form ? diffSettings(data.settings, form) : []),
    [data, form],
  );
  const changed = useMemo(
    () => (data && form ? diffMap(data.settings, form) : {}),
    [data, form],
  );
  const fieldErrors = useMemo(() => (form ? validateForm(form) : {}), [form]);
  const invalid = Object.keys(fieldErrors).length > 0;
  const dirty = diffs.length > 0;

  useUnsavedGuard(dirty);

  // Scrollspy: highlight the section currently in view.
  useEffect(() => {
    const sections = Array.from(document.querySelectorAll('[data-settings-section]'));
    if (sections.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActiveSection(entry.target.id);
        }
      },
      { rootMargin: '-20% 0px -70% 0px' },
    );
    sections.forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, [data, keysData]);

  const patch = (section: keyof SettingsForm, key: string, value: unknown) => {
    setForm((f) =>
      f ? { ...f, [section]: { ...(f[section] as object), [key]: value } } : f,
    );
    setNotice(null);
  };

  const reset = (section: keyof SettingsForm, key: string) => {
    if (!data) return;
    patch(section, key, (data.settings[section] as Record<string, unknown>)[key]);
  };

  const patchScreen = (key: string, value: unknown) => {
    setForm((f) =>
      f
        ? {
            ...f,
            universe: {
              ...f.universe,
              screen: { ...f.universe.screen, [key]: value },
            },
          }
        : f,
    );
    setNotice(null);
  };

  const resetScreen = (key: string) => {
    if (!data) return;
    patchScreen(
      key,
      (data.settings.universe.screen as Record<string, unknown>)[key],
    );
  };

  const patchQuiet = (key: 'start' | 'end', value: string) => {
    if (!form) return;
    patch('notifications', 'quiet_hours', {
      ...form.notifications.quiet_hours,
      [key]: value,
    });
  };

  const toggleChannel = (key: string, channel: 'browser' | 'email') => {
    if (!form) return;
    const current = form.notifications.events[key] ?? [];
    patch('notifications', 'events', {
      ...form.notifications.events,
      [key]: current.includes(channel)
        ? current.filter((c) => c !== channel)
        : [...current, channel],
    });
  };

  const resetEventChannels = (key: string) => {
    if (!data || !form) return;
    patch('notifications', 'events', {
      ...form.notifications.events,
      [key]: [...(data.settings.notifications.events[key] ?? [])],
    });
  };

  const addSymbol = () => {
    if (!form || !newSymbol.trim() || !newSector) return;
    const symbol = newSymbol.trim().toUpperCase();
    if (form.universe.symbols.includes(symbol)) return;
    patch('universe', 'symbols', [...form.universe.symbols, symbol]);
    patch('universe', 'sectors', { ...form.universe.sectors, [symbol]: newSector });
    setNewSymbol('');
    setNewSector('');
  };

  const removeSymbol = (symbol: string) => {
    if (!form) return;
    patch(
      'universe',
      'symbols',
      form.universe.symbols.filter((s) => s !== symbol),
    );
    const sectors = { ...form.universe.sectors };
    delete sectors[symbol];
    patch('universe', 'sectors', sectors);
  };

  const discard = () => {
    if (!data) return;
    setForm(structuredClone(data.settings));
    setReason('');
  };

  const save = () => {
    if (!form || !reason.trim() || invalid) return;
    setSaving(true);
    setErrors([]);
    // Only the changed fields go up — the server validates a partial
    // tree and merges it onto the active settings. Read-only rows and
    // unchanged values are never part of a save.
    const submission: Record<string, Record<string, unknown>> = {};
    const universeSub: Record<string, unknown> = {};
    const screenSub: Record<string, unknown> = {};
    let universeListChanged = false;
    for (const path of Object.keys(changed)) {
      const [section, key] = path.split('.', 2);
      if (section === 'universe') {
        if (path === 'universe.mode') {
          universeSub.mode = form.universe.mode;
        } else if (key === 'screen') {
          const leaf = path.split('.')[2];
          screenSub[leaf] = (
            form.universe.screen as Record<string, unknown>
          )[leaf];
        } else {
          universeListChanged = true;
        }
        continue;
      }
      (submission[section] ??= {})[key] = (
        form[section as keyof SettingsForm] as Record<string, unknown>
      )[key];
    }
    if (universeListChanged) {
      universeSub.symbols = form.universe.symbols;
      universeSub.sectors = form.universe.sectors;
    }
    if (Object.keys(screenSub).length > 0) universeSub.screen = screenSub;
    if (Object.keys(universeSub).length > 0) submission.universe = universeSub;
    postSettings(submission, reason.trim())
      .then((res) => {
        setForm(null);
        setReason('');
        setNotice(`Saved as v${res.version} · applies at the next cycle.`);
        refresh();
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setSaving(false));
  };

  const doReset = () => {
    setResetBusy(true);
    setErrors([]);
    postPaperReset()
      .then((res) => {
        setResetOpen(false);
        setResetStep2(res.message);
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setResetBusy(false));
  };

  const startRun = () => {
    setResetBusy(true);
    postStartRun()
      .then((res) => {
        setResetStep2(null);
        setNotice(res.message ?? 'New run started.');
        refresh();
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setResetBusy(false));
  };

  if (!data || !form) return null;
  const s = form;
  const held = new Set(data.held_symbols);

  const sectionChanges = (id: string) =>
    Object.keys(changed).filter((path) => {
      if (path.startsWith('universe.')) return id === 'trading';
      if (path.startsWith('notifications.')) return id === 'notify';
      return SECTION_OF[path] === id;
    }).length;

  const visibleSections = SECTIONS.filter((section) => {
    if (!find.trim()) return true;
    const query = find.trim().toLowerCase();
    return `${section.label} ${SEARCH_TEXT[section.id] ?? ''}`.toLowerCase().includes(query);
  });

  const suffix = (path: string) => UNITS[path] ?? '';
  const barText = diffs
    .map((d) => `${d.label} ${d.from}${suffix(labelToPath(d.label))} → ${d.to}${suffix(labelToPath(d.label))}`)
    .join(' · ');

  function labelToPath(label: string): string {
    const entry = Object.entries(LABEL_OF).find(([, l]) => l === label);
    return entry ? entry[0] : '';
  }

  const row = (
    path: string,
    props: {
      id?: string;
      label: string;
      help: string;
      defaultText?: string;
      locked?: boolean;
      children: ReactNode;
    },
  ) => (
    <Row
      key={path}
      id={props.id}
      label={props.label}
      help={props.help}
      changed={path in changed}
      changedFrom={changed[path] ? `${changed[path]}${suffix(path)}` : undefined}
      error={fieldErrors[path]}
      locked={props.locked}
      pendingText={pendingText(path)}
      defaultText={props.defaultText ?? (path in DEFAULTS ? DEFAULTS[path] : undefined)}
      onReset={() => {
        const parts = path.split('.');
        if (parts.length === 3 && parts[0] === 'universe') {
          resetScreen(parts[2]);
          return;
        }
        const [section, key] = parts as [keyof SettingsForm, string];
        reset(section, key);
      }}
    >
      {props.children}
    </Row>
  );

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Settings</h2>
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          Changes apply from the next cycle · keys are saved separately
        </div>
      </div>

      {errors.length > 0 && <Banner variant="negative">{errors.join(' ')}</Banner>}
      {notice && <Banner variant="info">{notice}</Banner>}
      {data.pending_version !== null && (
        <Banner variant="info">
          v{data.pending_version} is pending — it applies at the next cycle
          {data.pending_change ? `: ${data.pending_change}` : '.'}
        </Banner>
      )}

      {/* Phone width: the section list becomes a horizontal scroll row. */}
      <style>{`@media (max-width: 760px) { nav.sn { position: static !important; max-width: none !important; flex: 1 1 100% !important; flex-direction: row !important; flex-wrap: nowrap; overflow-x: auto; gap: 6px !important; padding-bottom: 4px } nav.sn a { flex: 0 0 auto; white-space: nowrap; border: 1px solid var(--sa-border) } nav.sn .sn-hint, nav.sn .sn-find { display: none } }`}</style>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-6)', alignItems: 'flex-start' }}>
        <nav
          className="sn"
          aria-label="Settings sections"
          style={{ flex: '1 1 200px', maxWidth: 240, position: 'sticky', top: 24, display: 'flex', flexDirection: 'column', gap: 2 }}
        >
          <input
            className="sn-find"
            type="search"
            aria-label="Find a setting"
            placeholder="Find a setting"
            value={find}
            onChange={(e) => setFind(e.target.value)}
            style={{ ...textInput(), marginBottom: 10, width: '100%' }}
          />
          {visibleSections.map((section) => {
            const count = sectionChanges(section.id);
            const active = activeSection === section.id;
            return (
              <a
                key={section.id}
                href={`#${section.id}`}
                onClick={() => setActiveSection(section.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  minHeight: 36,
                  padding: '0 12px',
                  borderRadius: 'var(--sa-radius-sm)',
                  textDecoration: 'none',
                  fontSize: 'var(--sa-text-base)',
                  background: active ? 'var(--sa-raised)' : 'transparent',
                  color: active ? 'var(--sa-text)' : 'var(--sa-text-2)',
                  boxShadow: active ? 'inset 2px 0 0 var(--sa-accent)' : 'none',
                }}
              >
                {section.label}
                {count > 0 && (
                  <span
                    title={`${count} unsaved change${count === 1 ? '' : 's'}`}
                    style={{
                      minWidth: 20,
                      height: 20,
                      padding: '0 6px',
                      boxSizing: 'border-box',
                      borderRadius: 999,
                      background: 'var(--sa-accent-bg)',
                      border: '1px solid var(--sa-accent-border)',
                      color: 'var(--sa-accent)',
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 11,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    {count}
                  </span>
                )}
              </a>
            );
          })}
          {visibleSections.length === 0 && (
            <div style={{ padding: '8px 12px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
              No settings match “{find}”
            </div>
          )}
          <div className="sn-hint" style={{ marginTop: 14, padding: 12, border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
            Saved settings become a new version and apply from the next cycle. Current:{' '}
            <b style={{ color: 'var(--sa-text-2)', fontWeight: 600 }}>
              v{data.active_version ?? '—'}
            </b>
          </div>
        </nav>

        <div style={{ flex: '999 1 560px', minWidth: 0, maxWidth: 920, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
          {keysData && (
            <KeysSection
              keys={keysData}
              onChanged={refreshKeys}
              onNotice={setNotice}
              onError={setErrors}
            />
          )}

          <Section id="trading" title="Trading" blurb="How orders are sent and which symbols are watched.">
            <Row
              id="mode"
              label="Execution mode"
              help={
                s.execution.mode === 'supervised'
                  ? 'Every entry waits for your approval on the Dashboard. Exits, stops and targets never wait.'
                  : 'Orders that pass every check and gate go to Alpaca straight away. Supervised: each entry waits for your approval.'
              }
              changed={'execution.mode' in changed}
              changedFrom={changed['execution.mode'] ? String(changed['execution.mode']) : undefined}
              defaultText={DEFAULTS['execution.mode']}
              pendingText={pendingText('execution.mode')}
              onReset={() => reset('execution', 'mode')}
            >
              <div role="radiogroup" aria-label="Execution mode" style={{ display: 'inline-flex', padding: 3, background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)' }}>
                {(['automatic', 'supervised'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    role="radio"
                    aria-checked={s.execution.mode === mode}
                    onClick={() => patch('execution', 'mode', mode)}
                    style={{
                      height: 38,
                      padding: '0 16px',
                      border: 0,
                      borderRadius: 'var(--sa-radius-sm)',
                      cursor: 'pointer',
                      fontSize: 'var(--sa-text-base)',
                      fontWeight: 600,
                      background: s.execution.mode === mode ? 'var(--sa-border-strong)' : 'transparent',
                      color: s.execution.mode === mode ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                      textTransform: 'capitalize',
                    }}
                  >
                    {mode}
                  </button>
                ))}
              </div>
            </Row>

            <Row
              id="umode"
              label="Universe"
              help={
                <>
                  Fixed list: the {s.universe.symbols.length} symbols below. Screened pool — a daily liquidity screen picks the eligible symbols from a pool of up to {universe?.pool?.max ?? 150} — arrives with PR 12; the screen already runs as a preview.{' '}
                  <Link to="/universe" style={{ color: 'var(--sa-accent)' }}>
                    Open Universe
                    {universe?.pool && universe?.counts
                      ? ` · ${universe.pool.size} in pool, ${universe.counts.eligible} eligible`
                      : ''}
                  </Link>
                </>
              }
              changed={'universe.mode' in changed}
              changedFrom={changed['universe.mode'] ? String(changed['universe.mode']) : undefined}
              defaultText={DEFAULTS['universe.mode']}
              pendingText={pendingText('universe.mode')}
              onReset={() => reset('universe', 'mode')}
            >
              <div role="radiogroup" aria-label="Universe" style={{ display: 'inline-flex', padding: 3, background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)' }}>
                {(
                  [
                    ['fixed', 'Fixed list', false],
                    ['screened', 'Screened pool', true],
                  ] as const
                ).map(([mode, label, unavailable]) => (
                  <button
                    key={mode}
                    type="button"
                    role="radio"
                    aria-checked={s.universe.mode === mode}
                    disabled={unavailable}
                    title={unavailable ? 'Screened pool arrives with PR 12' : undefined}
                    onClick={() => patch('universe', 'mode', mode)}
                    style={{
                      height: 38,
                      padding: '0 14px',
                      border: 0,
                      borderRadius: 'var(--sa-radius-sm)',
                      cursor: unavailable ? 'not-allowed' : 'pointer',
                      fontSize: 'var(--sa-text-base)',
                      fontWeight: 600,
                      background: s.universe.mode === mode ? 'var(--sa-border-strong)' : 'transparent',
                      color: s.universe.mode === mode ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                      opacity: unavailable ? 0.5 : 1,
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </Row>

            <h3 style={subHead}>
              Liquidity screen · {universe?.screen_time_ny ?? '08:10'} New York
            </h3>
            {row('universe.screen.min_price', {
              id: 'minpx',
              label: 'Min price',
              help: 'Lower-priced stocks are excluded.',
              children: (
                <NumBox
                  id="minpx"
                  label="Min price"
                  value={s.universe.screen.min_price}
                  unit="$"
                  accent={'universe.screen.min_price' in changed}
                  invalid={!!fieldErrors['universe.screen.min_price']}
                  onChange={(v) => patchScreen('min_price', v)}
                />
              ),
            })}
            {row('universe.screen.min_avg_dollar_volume', {
              id: 'adv',
              label: 'Min average dollar volume',
              help: 'Average of price × volume over the last sessions below.',
              children: (
                <NumBox
                  id="adv"
                  label="Min average dollar volume"
                  value={s.universe.screen.min_avg_dollar_volume / 1_000_000}
                  unit="$M"
                  accent={'universe.screen.min_avg_dollar_volume' in changed}
                  invalid={!!fieldErrors['universe.screen.min_avg_dollar_volume']}
                  onChange={(v) => patchScreen('min_avg_dollar_volume', v * 1_000_000)}
                />
              ),
            })}
            {row('universe.screen.lookback_sessions', {
              id: 'advn',
              label: 'Average over',
              help: 'Sessions used for the dollar volume and for the rank.',
              children: (
                <NumBox
                  id="advn"
                  label="Average over"
                  value={s.universe.screen.lookback_sessions}
                  unit="sessions"
                  accent={'universe.screen.lookback_sessions' in changed}
                  invalid={!!fieldErrors['universe.screen.lookback_sessions']}
                  onChange={(v) => patchScreen('lookback_sessions', v)}
                />
              ),
            })}
            {row('universe.screen.drop_after_failed_screens', {
              id: 'drop',
              label: 'Drop after',
              help: 'A symbol leaves the eligible list only after failing this many daily screens in a row.',
              children: (
                <NumBox
                  id="drop"
                  label="Drop after"
                  value={s.universe.screen.drop_after_failed_screens}
                  unit="days"
                  accent={'universe.screen.drop_after_failed_screens' in changed}
                  invalid={!!fieldErrors['universe.screen.drop_after_failed_screens']}
                  onChange={(v) => patchScreen('drop_after_failed_screens', v)}
                />
              ),
            })}
            <Row
              id="fit"
              label="One share fits in max position"
              help="A symbol whose price is above max position size can never be bought."
              locked
            >
              <Switch id="fit" on locked />
            </Row>
            <Row
              id="rank"
              label="Model-call order"
              help="Candidates that pass the rules get model calls in this order, after held positions."
              defaultText="Return vs SPY"
            >
              <select
                id="rank"
                aria-label="Model-call order"
                disabled
                style={{ ...selectStyle, minWidth: 200 }}
              >
                <option>Return vs SPY, 20 sessions</option>
              </select>
            </Row>

            <div style={{ padding: '14px 0', borderBottom: '1px solid var(--sa-border)', display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <span style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <span style={{ fontSize: 'var(--sa-text-base)' }}>Fixed list</span>
                  <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                    US stocks and ETFs tradable at Alpaca, long only. Held symbols (locked) can't be removed until closed.
                  </span>
                </span>
                <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                  <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>{s.universe.symbols.length} symbols</span>
                  {'universe.symbols' in pendingMap || Object.keys(pendingMap).some((p) => p.startsWith('universe.sector.')) ? (
                    <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-faint)' }}>
                      Pending: list change saved
                    </span>
                  ) : null}
                  {'universe.symbols' in changed || Object.keys(changed).some((p) => p.startsWith('universe.sector.')) ? (
                    <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-accent)' }}>
                      Changed from {data.settings.universe.symbols.length} symbols ·{' '}
                      <button
                        type="button"
                        onClick={() => {
                          patch('universe', 'symbols', [...data.settings.universe.symbols]);
                          patch('universe', 'sectors', { ...data.settings.universe.sectors });
                        }}
                        style={{ background: 'none', border: 0, color: 'var(--sa-accent)', cursor: 'pointer', fontSize: 'var(--sa-text-sm)', padding: 0 }}
                      >
                        Reset
                      </button>
                    </span>
                  ) : null}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {s.universe.symbols.map((symbol) => (
                  <span
                    key={symbol}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 8,
                      height: 36,
                      padding: '0 4px 0 12px',
                      borderRadius: 'var(--sa-radius-md)',
                      border: '1px solid var(--sa-border-strong)',
                      background: 'var(--sa-raised)',
                      fontSize: 'var(--sa-text-md)',
                    }}
                  >
                    <b style={{ fontWeight: 600 }}>{symbol}</b>
                    <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                      {s.universe.sectors[symbol] ?? 'no sector'}
                    </span>
                    {held.has(symbol) ? (
                      <span title="Held — locked until the position closes" style={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sa-text-muted)' }}>
                        <LockIcon />
                      </span>
                    ) : (
                      <button
                        type="button"
                        aria-label={`Remove ${symbol}`}
                        title={`Remove ${symbol}`}
                        onClick={() => removeSymbol(symbol)}
                        style={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', border: 0, borderRadius: 'var(--sa-radius-sm)', background: 'transparent', color: 'var(--sa-text-muted)', cursor: 'pointer' }}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <line x1="6" y1="6" x2="18" y2="18" />
                          <line x1="18" y1="6" x2="6" y2="18" />
                        </svg>
                      </button>
                    )}
                  </span>
                ))}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                <input
                  aria-label="Symbol to add"
                  placeholder="Symbol, e.g. AVGO"
                  value={newSymbol}
                  onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                  style={{ ...textInput(), width: 180, textTransform: 'uppercase' }}
                />
                <select
                  id="add-sector"
                  aria-label="Sector"
                  value={newSector}
                  onChange={(e) => setNewSector(e.target.value)}
                  style={{ ...selectStyle, color: newSector ? 'var(--sa-text)' : 'var(--sa-text-muted)' }}
                >
                  <option value="">Sector…</option>
                  {s.universe.allowed_sectors.map((sector) => (
                    <option key={sector} value={sector}>{sector}</option>
                  ))}
                </select>
                <Button onClick={addSymbol} disabled={!newSymbol.trim() || !newSector} style={{ height: 40 }}>
                  Add
                </Button>
              </div>
            </div>

            {row('performance.slippage_pct_per_side', {
              id: 'slip',
              label: 'Assumed slippage',
              help: 'Only used for the adjusted P&L in Performance. Orders are unaffected.',
              children: (
                <NumBox
                  id="slip"
                  value={s.performance.slippage_pct_per_side}
                  unit="% per side"
                  accent={'performance.slippage_pct_per_side' in changed}
                  invalid={!!fieldErrors['performance.slippage_pct_per_side']}
                  onChange={(v) => patch('performance', 'slippage_pct_per_side', v)}
                />
              ),
            })}
          </Section>

          <Section
            id="risk"
            title="Risk limits"
            right={sectionChanges('risk') > 0 ? `${sectionChanges('risk')} changed` : undefined}
            blurb="Checked in code after every model answer; the first one that fails blocks the action."
          >
            <h3 style={subHead}>Per trade</h3>
            {row('risk.risk_per_trade_pct', {
              id: 'rpt',
              label: 'Risk per trade',
              help: 'What you lose if the stop is hit. Shares = this budget ÷ (entry − stop).',
              children: (
                <NumBox id="rpt" value={s.risk.risk_per_trade_pct} unit="% of equity" accent={'risk.risk_per_trade_pct' in changed} invalid={!!fieldErrors['risk.risk_per_trade_pct']} onChange={(v) => patch('risk', 'risk_per_trade_pct', v)} />
              ),
            })}
            {row('risk.max_position_pct', {
              id: 'maxpos',
              label: 'Max position size',
              help: 'Shares are reduced to fit.',
              children: (
                <NumBox id="maxpos" value={s.risk.max_position_pct} unit="% of equity" accent={'risk.max_position_pct' in changed} invalid={!!fieldErrors['risk.max_position_pct']} onChange={(v) => patch('risk', 'max_position_pct', v)} />
              ),
            })}
            {row('risk.max_order_pct', {
              id: 'maxord',
              label: 'Max single order',
              help: 'Any larger order is blocked and alerts.',
              children: (
                <NumBox id="maxord" value={s.risk.max_order_pct} unit="% of equity" accent={'risk.max_order_pct' in changed} invalid={!!fieldErrors['risk.max_order_pct']} onChange={(v) => patch('risk', 'max_order_pct', v)} />
              ),
            })}

            <h3 style={subHead}>Portfolio</h3>
            {row('risk.max_open_risk_pct', {
              id: 'openrisk',
              label: 'Total open risk',
              help: 'Entry-to-stop risk of all positions and working entry orders together.',
              children: (
                <NumBox id="openrisk" value={s.risk.max_open_risk_pct} unit="% of equity" accent={'risk.max_open_risk_pct' in changed} invalid={!!fieldErrors['risk.max_open_risk_pct']} onChange={(v) => patch('risk', 'max_open_risk_pct', v)} />
              ),
            })}
            {row('risk.max_open_positions', {
              id: 'npos',
              label: 'Max open positions',
              help: 'Working entry orders count as positions.',
              children: (
                <NumBox id="npos" value={s.risk.max_open_positions} unit="positions" accent={'risk.max_open_positions' in changed} invalid={!!fieldErrors['risk.max_open_positions']} onChange={(v) => patch('risk', 'max_open_positions', v)} />
              ),
            })}
            {row('risk.max_per_sector', {
              id: 'nsec',
              label: 'Max positions per sector',
              help: 'QQQ counts as Technology, SPY as Index.',
              children: (
                <NumBox id="nsec" value={s.risk.max_per_sector} unit="per sector" accent={'risk.max_per_sector' in changed} invalid={!!fieldErrors['risk.max_per_sector']} onChange={(v) => patch('risk', 'max_per_sector', v)} />
              ),
            })}
            <Row label="Cash only" help="Order value must fit in cash. Margin is never used." locked id="cash">
              <Switch id="cash" on locked />
            </Row>

            <h3 style={subHead}>Brakes</h3>
            {row('risk.daily_loss_limit_pct', {
              id: 'dll',
              label: 'Daily loss limit',
              help: 'No new entries for the rest of the session once the day is this far down.',
              children: (
                <NumBox id="dll" value={s.risk.daily_loss_limit_pct} unit="% of equity" accent={'risk.daily_loss_limit_pct' in changed} invalid={!!fieldErrors['risk.daily_loss_limit_pct']} onChange={(v) => patch('risk', 'daily_loss_limit_pct', v)} />
              ),
            })}
            {row('risk.losing_streak', {
              id: 'streak',
              label: 'Losing streak',
              help: 'After this many losses in a row, risk per trade is halved until a win.',
              children: (
                <NumBox id="streak" value={s.risk.losing_streak} unit="trades" accent={'risk.losing_streak' in changed} invalid={!!fieldErrors['risk.losing_streak']} onChange={(v) => patch('risk', 'losing_streak', v)} />
              ),
            })}
            <Row
              label="Shock brake"
              help="No new entries this session when SPY drops this much in the last 1h bar or since the open."
              changed={'risk.shock_brake_1h_pct' in changed || 'risk.shock_brake_session_pct' in changed}
              changedFrom={`${data.settings.risk.shock_brake_1h_pct}% / ${data.settings.risk.shock_brake_session_pct}%`}
              error={fieldErrors['risk.shock_brake_1h_pct'] ?? fieldErrors['risk.shock_brake_session_pct']}
              defaultText="1.5% / 2.5%"
              pendingText={pendingJoined(
                ['risk.shock_brake_1h_pct', 'risk.shock_brake_session_pct'],
                ' / ',
                (v) => `${v}%`,
              )}
              onReset={() => {
                reset('risk', 'shock_brake_1h_pct');
                reset('risk', 'shock_brake_session_pct');
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <NumBox label="from" value={s.risk.shock_brake_1h_pct} width={60} invalid={!!fieldErrors['risk.shock_brake_1h_pct']} onChange={(v) => patch('risk', 'shock_brake_1h_pct', v)} />
                <span style={{ color: 'var(--sa-text-muted)' }}>to</span>
                <span style={inputBox(false, !!fieldErrors['risk.shock_brake_session_pct'])}>
                  <input
                    aria-label="to"
                    value={Number.isNaN(s.risk.shock_brake_session_pct) ? '' : s.risk.shock_brake_session_pct}
                    inputMode="decimal"
                    onChange={(e) => patch('risk', 'shock_brake_session_pct', Number(e.target.value))}
                    style={numInput(60)}
                  />
                  <span style={unitText}>% SPY</span>
                </span>
              </span>
            </Row>
            {row('risk.drawdown_kill_pct', {
              id: 'ddk',
              label: 'Drawdown kill',
              help: 'Kill switch turns on when equity is this far below its peak.',
              children: (
                <NumBox id="ddk" value={s.risk.drawdown_kill_pct} unit="% from peak" accent={'risk.drawdown_kill_pct' in changed} invalid={!!fieldErrors['risk.drawdown_kill_pct']} onChange={(v) => patch('risk', 'drawdown_kill_pct', v)} />
              ),
            })}
            {row('risk.max_orders_per_cycle', {
              id: 'opc',
              label: 'Max orders per cycle',
              help: 'Safety limit on order bursts.',
              children: (
                <NumBox id="opc" value={s.risk.max_orders_per_cycle} unit="orders" accent={'risk.max_orders_per_cycle' in changed} invalid={!!fieldErrors['risk.max_orders_per_cycle']} onChange={(v) => patch('risk', 'max_orders_per_cycle', v)} />
              ),
            })}
          </Section>

          <Section id="rules" title="Entry rules and checks" blurb="The pre-filter before a model call, and the checks on what the model proposes.">
            <h3 style={subHead}>Before the model is asked</h3>
            <Row
              label="RSI range (1h)"
              help="Outside this range the symbol is skipped without a model call. Inclusive."
              changed={'rules.rsi_min' in changed || 'rules.rsi_max' in changed}
              changedFrom={`${data.settings.rules.rsi_min}–${data.settings.rules.rsi_max}`}
              error={fieldErrors['rules.rsi_min']}
              defaultText="40–70"
              pendingText={pendingJoined(['rules.rsi_min', 'rules.rsi_max'], '–')}
              onReset={() => {
                reset('rules', 'rsi_min');
                reset('rules', 'rsi_max');
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <NumBox label="from" value={s.rules.rsi_min} width={60} invalid={!!fieldErrors['rules.rsi_min']} onChange={(v) => patch('rules', 'rsi_min', v)} />
                <span style={{ color: 'var(--sa-text-muted)' }}>to</span>
                <NumBox label="to" value={s.rules.rsi_max} width={60} invalid={!!fieldErrors['rules.rsi_max']} onChange={(v) => patch('rules', 'rsi_max', v)} />
              </span>
            </Row>

            <h3 style={subHead}>Checks on the model's answer</h3>
            {row('decision.min_confidence_enter', {
              id: 'conf',
              label: 'Min confidence to enter',
              help: 'Lower-confidence entries become a skip.',
              children: (
                <NumBox id="conf" value={s.decision.min_confidence_enter} width={80} accent={'decision.min_confidence_enter' in changed} invalid={!!fieldErrors['decision.min_confidence_enter']} onChange={(v) => patch('decision', 'min_confidence_enter', v)} />
              ),
            })}
            <Row
              label="Stop distance"
              help="Allowed distance between entry and stop."
              changed={'decision.stop_atr_min' in changed || 'decision.stop_atr_max' in changed}
              changedFrom={`${data.settings.decision.stop_atr_min}–${data.settings.decision.stop_atr_max}`}
              error={fieldErrors['decision.stop_atr_min']}
              defaultText="1.5–4 × ATR"
              pendingText={pendingJoined(
                ['decision.stop_atr_min', 'decision.stop_atr_max'],
                '–',
              )}
              onReset={() => {
                reset('decision', 'stop_atr_min');
                reset('decision', 'stop_atr_max');
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <NumBox label="from" value={s.decision.stop_atr_min} width={60} invalid={!!fieldErrors['decision.stop_atr_min']} onChange={(v) => patch('decision', 'stop_atr_min', v)} />
                <span style={{ color: 'var(--sa-text-muted)' }}>to</span>
                <span style={inputBox(false, !!fieldErrors['decision.stop_atr_max'])}>
                  <input
                    aria-label="to"
                    value={Number.isNaN(s.decision.stop_atr_max) ? '' : s.decision.stop_atr_max}
                    inputMode="decimal"
                    onChange={(e) => patch('decision', 'stop_atr_max', Number(e.target.value))}
                    style={numInput(60)}
                  />
                  <span style={unitText}>× ATR 1h</span>
                </span>
              </span>
            </Row>
            {row('decision.min_reward_risk', {
              id: 'rr',
              label: 'Min reward to risk',
              help: 'Target distance ÷ stop distance.',
              children: (
                <NumBox id="rr" value={s.decision.min_reward_risk} unit=": 1" accent={'decision.min_reward_risk' in changed} invalid={!!fieldErrors['decision.min_reward_risk']} onChange={(v) => patch('decision', 'min_reward_risk', v)} />
              ),
            })}

            <h3 style={subHead}>Orders</h3>
            {row('execution.max_spread_pct', {
              id: 'spread',
              label: 'Max spread',
              help: 'Entries are skipped when the quote spread is wider.',
              children: (
                <NumBox id="spread" value={s.execution.max_spread_pct} unit="% of price" accent={'execution.max_spread_pct' in changed} invalid={!!fieldErrors['execution.max_spread_pct']} onChange={(v) => patch('execution', 'max_spread_pct', v)} />
              ),
            })}
            {row('execution.entry_limit_offset_pct', {
              id: 'lim',
              label: 'Entry limit above ask',
              help: 'Limit price for the entry order.',
              children: (
                <NumBox id="lim" value={s.execution.entry_limit_offset_pct} unit="%" accent={'execution.entry_limit_offset_pct' in changed} invalid={!!fieldErrors['execution.entry_limit_offset_pct']} onChange={(v) => patch('execution', 'entry_limit_offset_pct', v)} />
              ),
            })}
            {row('execution.entry_timeout_minutes', {
              id: 'tmo',
              label: 'Cancel unfilled entry after',
              help: "An entry that hasn't filled is cancelled.",
              children: (
                <NumBox id="tmo" value={s.execution.entry_timeout_minutes} unit="min" accent={'execution.entry_timeout_minutes' in changed} invalid={!!fieldErrors['execution.entry_timeout_minutes']} onChange={(v) => patch('execution', 'entry_timeout_minutes', v)} />
              ),
            })}
          </Section>

          <Section id="earnings" title="Earnings" blurb="How the agent handles symbols around their earnings date.">
            {row('risk.earnings_blackout_days', {
              id: 'blackout',
              label: 'Blackout before earnings',
              help: 'No new entries when a symbol reports within this many trading days.',
              children: (
                <NumBox id="blackout" value={s.risk.earnings_blackout_days} unit="trading days" accent={'risk.earnings_blackout_days' in changed} invalid={!!fieldErrors['risk.earnings_blackout_days']} onChange={(v) => patch('risk', 'earnings_blackout_days', v)} />
              ),
            })}
            {row('risk.hold_through_earnings', {
              id: 'hold',
              label: 'Hold through earnings',
              help: 'Off: a held symbol is sold in the 15:46 cycle before it reports.',
              children: (
                <Switch id="hold" on={s.risk.hold_through_earnings} onChange={(v) => patch('risk', 'hold_through_earnings', v)} />
              ),
            })}
          </Section>

          <Section id="model" title="Model and reviews" blurb="The model, its prompt and limits on calls and cost.">
            <h3 style={subHead}>Decisions</h3>
            {row('decision.model', {
              id: 'model',
              label: 'Model',
              help: 'Used for decisions and reviews.',
              children: (
                <select
                  id="model"
                  value={s.decision.model}
                  onChange={(e) => patch('decision', 'model', e.target.value)}
                  style={selectStyle}
                >
                  {[s.decision.model, 'gpt-4o-mini']
                    .filter((v, i, a) => a.indexOf(v) === i)
                    .map((v) => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                </select>
              ),
            })}
            {row('decision.prompt', {
              id: 'prompt',
              label: 'Decision prompt',
              help: 'Prompts are never edited; a change is a new version.',
              defaultText: undefined,
              children: (
                <select
                  id="prompt"
                  value={s.decision.prompt}
                  onChange={(e) => patch('decision', 'prompt', e.target.value)}
                  style={selectStyle}
                >
                  {[s.decision.prompt, ...data.prompt_options.map((p) => p.version)]
                    .filter((v, i, a) => a.indexOf(v) === i)
                    .map((v) => {
                      const opt = data.prompt_options.find((p) => p.version === v);
                      return <option key={v} value={v}>{opt ? `${opt.version} · ${opt.hash}` : v}</option>;
                    })}
                </select>
              ),
            })}
            {row('decision.temperature', {
              id: 'temp',
              label: 'Temperature',
              help: 'Decisions use 0 so the same input gives the same answer.',
              children: (
                <NumBox id="temp" value={s.decision.temperature} width={60} accent={'decision.temperature' in changed} invalid={!!fieldErrors['decision.temperature']} onChange={(v) => patch('decision', 'temperature', v)} />
              ),
            })}
            {row('decision.max_calls_per_cycle', {
              id: 'calls',
              label: 'Max model calls per cycle',
              help: 'Held positions are called first.',
              children: (
                <NumBox id="calls" value={s.decision.max_calls_per_cycle} unit="calls" accent={'decision.max_calls_per_cycle' in changed} invalid={!!fieldErrors['decision.max_calls_per_cycle']} onChange={(v) => patch('decision', 'max_calls_per_cycle', v)} />
              ),
            })}

            <h3 style={subHead}>Reviews</h3>
            {row('reviews.briefing_enabled', {
              id: 'brief',
              label: 'Daily briefing',
              help: 'Trading days at 09:00 New York (15:00 Amsterdam).',
              children: (
                <Switch id="brief" on={s.reviews.briefing_enabled} onChange={(v) => patch('reviews', 'briefing_enabled', v)} />
              ),
            })}
            {row('reviews.monthly_cost_cap_usd', {
              id: 'rcap',
              label: 'Review cost cap',
              help: `Separate from decisions.${data.reviews_spend_usd ? ` This month: $${Number(data.reviews_spend_usd).toFixed(2)} of $${s.reviews.monthly_cost_cap_usd}.` : ''} Over the cap, reviews use the plain template.`,
              children: (
                <NumBox id="rcap" value={s.reviews.monthly_cost_cap_usd} unit="$ / month" width={60} accent={'reviews.monthly_cost_cap_usd' in changed} invalid={!!fieldErrors['reviews.monthly_cost_cap_usd']} onChange={(v) => patch('reviews', 'monthly_cost_cap_usd', v)} />
              ),
            })}
          </Section>

          <Section
            id="notify"
            title="Notifications"
            right="browser · email"
            blurb="What the agent tells you about, and where. Critical alerts always reach the browser."
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, padding: '8px 0 4px' }}>
              <EventChannelTable
                events={data.notification_events}
                channels={s.notifications?.events ?? {}}
                changed={changed}
                onToggle={toggleChannel}
                onReset={resetEventChannels}
              />
              <div style={{ flex: '2 1 300px', minWidth: 0 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <BrowserNotifyCard />
                  <SmtpCard
                    smtp={data.smtp}
                    onSaved={refresh}
                    onNotice={setNotice}
                    onError={setErrors}
                  />
                </div>
              </div>
            </div>

            {row('notifications.repeat_limit_minutes', {
              id: 'throttle',
              label: 'Repeat limit',
              help: 'The same alert is sent at most once per this many minutes. Critical alerts are never held back.',
              children: (
                <NumBox
                  id="throttle"
                  value={s.notifications?.repeat_limit_minutes ?? 30}
                  unit="min"
                  accent={'notifications.repeat_limit_minutes' in changed}
                  invalid={!!fieldErrors['notifications.repeat_limit_minutes']}
                  onChange={(v) => patch('notifications', 'repeat_limit_minutes', v)}
                />
              ),
            })}

            <Row
              label="Quiet hours (Amsterdam)"
              help="Only critical alerts are sent in this window; the rest wait until it ends."
              changed={
                'notifications.quiet_hours.start' in changed ||
                'notifications.quiet_hours.end' in changed
              }
              changedFrom={`${data.settings.notifications.quiet_hours.start}–${data.settings.notifications.quiet_hours.end}`}
              error={
                fieldErrors['notifications.quiet_hours.start'] ??
                fieldErrors['notifications.quiet_hours.end']
              }
              defaultText="23:00–08:00"
              pendingText={pendingJoined(
                ['notifications.quiet_hours.start', 'notifications.quiet_hours.end'],
                '–',
              )}
              onReset={() => reset('notifications', 'quiet_hours')}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span style={inputBox(
                  'notifications.quiet_hours.start' in changed,
                  !!fieldErrors['notifications.quiet_hours.start'],
                )}>
                  <input
                    aria-label="from"
                    value={s.notifications?.quiet_hours?.start ?? ''}
                    onChange={(e) => patchQuiet('start', e.target.value)}
                    style={numInput(60)}
                  />
                </span>
                <span style={{ color: 'var(--sa-text-muted)' }}>to</span>
                <span style={inputBox(
                  'notifications.quiet_hours.end' in changed,
                  !!fieldErrors['notifications.quiet_hours.end'],
                )}>
                  <input
                    aria-label="to"
                    value={s.notifications?.quiet_hours?.end ?? ''}
                    onChange={(e) => patchQuiet('end', e.target.value)}
                    style={numInput(60)}
                  />
                </span>
              </span>
            </Row>

            <h3 style={subHead}>Recently sent</h3>
            <RecentNotifications />
          </Section>

          <Section
            id="schedule"
            title="Schedule"
            right={`${data.schedule.cycles_per_session} cycles per session`}
            blurb="Cycle times follow the New York exchange clock and are fixed by the spec."
          >
            <div style={{ padding: '8px 0 4px' }}>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', minWidth: 520, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      {['Cycle', 'New York', 'Amsterdam', data.schedule.shifted_label ?? '', 'Bar used', 'New entries'].map((h, i) => (
                        <th key={i} scope="col" style={{ textAlign: 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.schedule.rows.map((scheduleRow) => (
                      <tr key={scheduleRow.index}>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>{scheduleRow.index}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>{scheduleRow.ny}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>{scheduleRow.amsterdam}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-muted)' }}>{scheduleRow.amsterdam_shifted ?? '—'}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>{scheduleRow.bar_used}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top' }}>
                          {scheduleRow.entries}
                          {scheduleRow.entries === 'no' && <span style={{ color: 'var(--sa-text-muted)' }}> · exits and earnings exits only</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5, paddingBottom: 14 }}>
              Position checks run at :01, :16 and :31 between cycles. From 26 to 30 October Amsterdam
              times are an hour earlier (the EU changes clocks a week before the US). Early-close days
              run only the cycles before 13:00 New York.
            </div>
          </Section>

          <Section
            id="history"
            title="Settings history"
            right={data.active_version === null ? 'no saved versions' : `current: v${data.active_version}`}
            blurb="Every saved change is a version with its reason. Decisions record the version they ran under."
          >
            <div style={{ padding: '8px 0 14px' }}>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', minWidth: 520, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      {['Version', 'When', 'Change', 'Reason'].map((h) => (
                        <th key={h} scope="col" style={{ textAlign: 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.history.map((historyRow) => (
                      <tr key={historyRow.version}>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>v{historyRow.version}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', fontFamily: 'var(--sa-font-mono)' }}>{displayDateTime(historyRow.changed_at)}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top' }}>{historyRow.change}</td>
                        <td style={{ textAlign: 'left', padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top', color: 'var(--sa-text-muted)' }}>{historyRow.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </Section>

          <Section id="danger" title="Paper account" blurb="Starts over with a clean account and a new performance run.">
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '12px 20px', padding: '14px 0 18px' }}>
              <div style={{ flex: '1 1 360px', display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontSize: 'var(--sa-text-base)' }}>Reset paper account</span>
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
                  Turns the kill switch on, closes every position, cancels every order and starts a new
                  performance run. You then reset the balance to $10,000 in the Alpaca dashboard. Past
                  decisions stay in the log.
                </span>
              </div>
              {resetStep2 ? (
                <Button variant="primary" onClick={startRun} disabled={resetBusy}>
                  Start new run
                </Button>
              ) : (
                <Button variant="danger" onClick={() => setResetOpen(true)} style={{ height: 44 }}>
                  Reset paper account…
                </Button>
              )}
            </div>
            {resetStep2 && (
              <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', paddingBottom: 14 }}>{resetStep2}</div>
            )}
          </Section>

          {dirty && (
            <div
              role="region"
              aria-label="Unsaved changes"
              style={{
                position: 'sticky',
                bottom: 16,
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: '12px 16px',
                padding: '12px 16px',
                background: 'var(--sa-chrome)',
                border: '1px solid var(--sa-accent-border)',
                borderRadius: 'var(--sa-radius-lg)',
                boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
              }}
            >
              <div style={{ flex: '2 1 320px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 'var(--sa-text-base)', fontWeight: 600 }}>
                  {diffs.length} unsaved change{diffs.length === 1 ? '' : 's'}
                </span>
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}>{barText}</span>
              </div>
              <input
                aria-label="Reason for this change"
                placeholder="Reason (shown in history)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                style={{ ...textInput('1 1 220px'), borderColor: reason.trim() ? 'var(--sa-border-strong)' : 'var(--sa-accent-border)' }}
              />
              <Button onClick={discard}>Discard</Button>
              <Button
                variant="primary"
                onClick={save}
                disabled={saving || invalid || !reason.trim()}
                title={
                  invalid
                    ? 'Fix the invalid values first'
                    : !reason.trim()
                      ? 'A reason is required — it shows in History'
                      : undefined
                }
              >
                {saving ? 'Saving…' : `Save as v${data.next_version}`}
              </Button>
            </div>
          )}
        </div>
      </div>

      <ResetAccountDialog
        open={resetOpen}
        busy={resetBusy}
        onCancel={() => setResetOpen(false)}
        onConfirm={doReset}
      />
    </div>
  );
}

// label (as produced by diffSettings) -> path, for unit suffixes in the bar
const LABEL_OF: Record<string, string> = {
  'performance.slippage_pct_per_side': 'Assumed slippage',
  'execution.mode': 'Execution mode',
  'execution.entry_limit_offset_pct': 'Entry limit above ask',
  'execution.entry_timeout_minutes': 'Cancel unfilled entry after',
  'execution.max_spread_pct': 'Max spread',
  'rules.rsi_min': 'RSI from',
  'rules.rsi_max': 'RSI to',
  'decision.model': 'Model',
  'decision.temperature': 'Temperature',
  'decision.prompt': 'Prompt version',
  'decision.min_confidence_enter': 'Min confidence to enter',
  'decision.stop_atr_min': 'Stop distance from',
  'decision.stop_atr_max': 'Stop distance to',
  'decision.min_reward_risk': 'Min reward to risk',
  'decision.max_calls_per_cycle': 'Max model calls per cycle',
  'risk.risk_per_trade_pct': 'Risk per trade',
  'risk.max_position_pct': 'Max position size',
  'risk.max_open_risk_pct': 'Total open risk',
  'risk.max_open_positions': 'Max open positions',
  'risk.max_per_sector': 'Max positions per sector',
  'risk.daily_loss_limit_pct': 'Daily loss limit',
  'risk.losing_streak': 'Losing streak',
  'risk.shock_brake_1h_pct': 'Shock brake, last 1h',
  'risk.shock_brake_session_pct': 'Shock brake, since open',
  'risk.earnings_blackout_days': 'Blackout before earnings',
  'risk.hold_through_earnings': 'Hold through earnings',
  'risk.drawdown_kill_pct': 'Drawdown kill',
  'risk.max_order_pct': 'Max single order',
  'risk.max_orders_per_cycle': 'Max orders per cycle',
  'reviews.briefing_enabled': 'Daily briefing',
  'reviews.monthly_cost_cap_usd': 'Review cost cap',
  'universe.mode': 'Universe mode',
  'universe.screen.min_price': 'Min price',
  'universe.screen.min_avg_dollar_volume': 'Min average dollar volume',
  'universe.screen.lookback_sessions': 'Average over',
  'universe.screen.drop_after_failed_screens': 'Drop after',
  'notifications.repeat_limit_minutes': 'Repeat limit',
  'notifications.quiet_hours.start': 'Quiet hours from',
  'notifications.quiet_hours.end': 'Quiet hours to',
};
