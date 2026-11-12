import type { SettingsPreview } from '../../types/api';
import { fmtUsd } from './useRiskPreview';

// Risk-limits preview display (item 069): the "= $40.74" amounts beside
// "% of equity" controls and the "What these limits mean today" box.
// The debounced fetch lives in useRiskPreview.ts.

function fmtPct(value: string | number): string {
  const n = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(n) ? `${n}%` : '—';
}

/** "= $40.74" beside a "% of equity" control; polite live region. */
export function UsdAmount({
  path,
  preview,
}: {
  path: string;
  preview: SettingsPreview | null;
}) {
  const amount = preview?.amounts[path];
  if (amount === undefined) return null;
  return (
    <span
      aria-live="polite"
      title={`At current equity ${preview ? fmtUsd(preview.equity) : ''}`}
      style={{
        fontFamily: 'var(--sa-font-mono)',
        fontSize: 'var(--sa-text-sm)',
        color: 'var(--sa-text-muted)',
        whiteSpace: 'nowrap',
      }}
    >
      = {fmtUsd(amount)}
    </span>
  );
}

/** "What these limits mean today" — the three-sentence summary on
 * Risk limits, straight from the preview numbers. */
export function RiskLimitsSummary({
  preview,
}: {
  preview: SettingsPreview | null;
}) {
  if (!preview) return null;
  const s = preview.summary;
  const binds =
    s.binds_first === 'positions'
      ? 'the position count binds first'
      : 'the open-risk limit binds first';
  return (
    <div
      style={{
        margin: '4px 0 10px',
        padding: '12px 14px',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        background: 'var(--sa-raised)',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <span style={{ fontSize: 'var(--sa-text-sm)', fontWeight: 600, color: 'var(--sa-text)' }}>
        What these limits mean today
      </span>
      <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
        One stopped trade costs about {fmtUsd(s.loss_per_trade_usd)}; after{' '}
        {s.losing_streak_len} losing trades in a row the risk budget halves to{' '}
        {fmtUsd(s.loss_per_trade_streak_usd)}.
      </span>
      <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
        {s.max_open_positions} positions at {fmtPct(s.risk_per_trade_pct)} each risk{' '}
        {fmtPct(s.positions_risk_pct)} of equity against the{' '}
        {fmtPct(s.open_risk_limit_pct)} open-risk limit — {binds}.
      </span>
      <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
        The day stops new entries at −{fmtUsd(s.daily_loss_stop_usd)}; the kill
        switch arms at {fmtUsd(s.kill_switch_equity_usd)},{' '}
        {fmtPct(s.drawdown_kill_pct)} below the {fmtUsd(s.peak_equity_usd)} peak.
      </span>
    </div>
  );
}
