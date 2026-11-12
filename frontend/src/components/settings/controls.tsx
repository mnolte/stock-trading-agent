import type { ReactNode } from 'react';

import { CardBoundary } from '../CardBoundary';
import { inputBox, numInput, rowFrame, unitText } from './styles';


// Shared building blocks for the Settings page (item 069 split): the
// design-token styles and the Section / Row / NumBox / Switch primitives
// every section composes.

export function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 12px' }}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function Section({
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
      <CardBoundary name={title}>
        {children}
      </CardBoundary>
    </section>
  );
}

export function Row({
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
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.45 }}>
          {help}
          {defaultText !== undefined && (
            <span style={{ color: 'var(--sa-text-faint)', whiteSpace: 'nowrap' }}> · default {defaultText}</span>
          )}
        </span>
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
        ) : null}
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

export function NumBox({
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

export function Switch({
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
