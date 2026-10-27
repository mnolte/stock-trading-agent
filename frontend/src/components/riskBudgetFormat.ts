import type { BudgetMeter, RiskBudgetResponse } from '../types/api';

// Display formatting for the Risk budget card. All figures come from
// /api/risk-budget — the same values the entry gates see; nothing here
// recomputes a limit. Text is copied from design/screens/Main.dc.html.

const MINUS = '−'; // U+2212, as in the design's "of −2% limit"

/** Trim a Decimal string for display: "2.50" → "2.5", "8" → "8". */
export function trimDecimal(v: string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? String(n) : v;
}

/** Two-decimal percent: "0.34" → "0.34%", null → "—". */
export function pct2(v: string | null | undefined): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(2)}%` : '—';
}

/** Signed percent with the design's minus: "+0.12%", "−0.62%", "+0.00%". */
export function signedPct(v: string | null | undefined): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const sign = n < 0 ? MINUS : '+';
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}

/** Drawdown meter value: "−0.28%" when under the peak, "0.00%" at it. */
export function drawdownUsed(meter: BudgetMeter): string {
  if (meter.used_pct === null) return '—';
  const n = Number(meter.used_pct);
  if (!Number.isFinite(n) || n <= 0) return '0.00%';
  return `${MINUS}${n.toFixed(2)}%`;
}

export function roomText(room: RiskBudgetResponse['room']): string {
  const base = `${room.trades} more trade${room.trades === 1 ? '' : 's'}`;
  if (room.limited_by === 'cash') return `${base} · limited by cash`;
  if (room.limited_by === 'open_risk') return `${base} · limited by open risk`;
  return base;
}

export function sectorsText(sectors: RiskBudgetResponse['sectors']): string {
  if (sectors.length === 0) return '—';
  return sectors.map((s) => `${s.sector} ${s.used}/${s.cap}`).join(' · ');
}

export function riskPerTradeText(b: RiskBudgetResponse): string {
  return (
    `${trimDecimal(b.risk_per_trade_pct)}% · losing streak ` +
    `${b.losing_streak.count} of ${b.losing_streak.max}`
  );
}

export function shockBrakeText(b: RiskBudgetResponse): string {
  const sb = b.shock_brake;
  const moves: string[] = [];
  if (sb.spy_1h_pct !== null) moves.push(`${signedPct(sb.spy_1h_pct)} last 1h`);
  if (sb.spy_session_pct !== null) {
    moves.push(`${signedPct(sb.spy_session_pct)} since open`);
  }
  return `${sb.on ? 'on' : 'off'}${moves.length ? ` · SPY ${moves.join(', ')}` : ''}`;
}

/** Re-entry row: "none today", the blocked symbols, or the setting state. */
export function reEntryText(b: RiskBudgetResponse, blocked: string[]): string {
  if (b.re_entry.allowed) return 'allowed by settings';
  return blocked.length ? blocked.join(' · ') : 'none today';
}

// Hover explanations, one per label, copied from the design. Values that
// come from settings (limits, streak length) are interpolated so the text
// stays true when settings change.

export const budgetHints = {
  openRisk:
    'What you would lose if every open position and working entry hit its stop.',
  dailyLoss: (limitPct: string): string =>
    `Loss in the current session. At ${MINUS}${trimDecimal(limitPct)}% ` +
    'no new entries for the rest of the session.',
  drawdown: (killPct: string): string =>
    'How far equity is below its highest point in this run. ' +
    `At ${MINUS}${trimDecimal(killPct)}% the kill switch turns on ` +
    'and everything is closed.',
  room:
    'The smallest of: positions left, open risk left ÷ risk per trade, ' +
    'and cash ÷ max position size.',
  sectors: (cap: number): string =>
    `At most ${cap} positions per sector; working entries count.`,
  riskPerTrade: (streak: { max: number; factor: string }): string =>
    `After ${streak.max} losses in a row risk per trade is ${
      Number(streak.factor) === 0.5
        ? 'halved'
        : `multiplied by ${trimDecimal(streak.factor)}`
    } until a win.`,
  shockBrake: (limit1h: string, limitSession: string): string =>
    `No new entries this session when SPY drops ${trimDecimal(limit1h)}% ` +
    `in a 1h bar or ${trimDecimal(limitSession)}% since the open.`,
  reEntry:
    'A symbol sold or stopped out today is not bought again until the next session.',
};
