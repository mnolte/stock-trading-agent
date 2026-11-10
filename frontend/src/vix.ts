import type { VixPayload } from './types/api';
import { fmtWeekdayDayShort, signed } from './format';

/** "17.4" — the VIX close at one decimal (design/DESIGN.md numbers). */
export function fmtVixClose(close: string | null | undefined): string {
  if (close === null || close === undefined) return '—';
  const n = Number(close);
  return Number.isFinite(n) ? n.toFixed(1) : '—';
}

/** "42nd" — ordinal for the percentile labels and tooltips. */
export function ordinal(n: number): string {
  const s = Math.round(n);
  const mod100 = s % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${s}th`;
  switch (s % 10) {
    case 1:
      return `${s}st`;
    case 2:
      return `${s}nd`;
    case 3:
      return `${s}rd`;
    default:
      return `${s}th`;
  }
}

/** "+1.2%" / "−6%" — one decimal, a trailing .0 trimmed (design mock). */
export function fmtVixPct(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const s = signed(n, 1);
  return `${s.endsWith('.0') ? s.slice(0, -2) : s}%`;
}

function pctNum(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** "VIX 17.4 · 42nd pct" — the latest-decision chip label (060). */
export function vixChipLabel(vix: VixPayload): string | null {
  if (vix.close === null) return null;
  const pct = pctNum(vix.percentile);
  return pct === null
    ? `VIX ${fmtVixClose(vix.close)}`
    : `VIX ${fmtVixClose(vix.close)} · ${ordinal(pct)} pct`;
}

function vixyTail(vix: VixPayload, sincePrefix: string): string | null {
  const since = fmtVixPct(vix.vixy_since_vix_pct);
  const today = fmtVixPct(vix.vixy_today_pct);
  const parts: string[] = [];
  if (since !== null) parts.push(`${sincePrefix} ${since}`);
  if (today !== null) parts.push(`today ${today}`);
  return parts.length ? parts.join(', ') : null;
}

/** The header's hover text (design/screens/Main.dc.html). */
export function vixHeaderTitle(vix: VixPayload): string {
  if (vix.stale) {
    const day = vix.date ? fmtWeekdayDayShort(vix.date) : null;
    return day === null
      ? 'FRED has not posted a newer close; the stored close is kept and marked stale'
      : `FRED has not posted a newer close; the stored close of ${day} is kept and marked stale`;
  }
  const segments: string[] = [];
  if (vix.date !== null) {
    segments.push(
      `Close of ${fmtWeekdayDayShort(vix.date)} from FRED (it posts a close the next afternoon)`,
    );
  } else {
    segments.push('Close from FRED (it posts a close the next afternoon)');
  }
  const pct = pctNum(vix.percentile);
  if (pct !== null) segments.push(`${ordinal(pct)} percentile of the last year`);
  const chg = fmtVixPct(vix.change_5d_pct);
  if (chg !== null) segments.push(`5 sessions ${chg}`);
  const tail = vixyTail(vix, 'VIXY since that close');
  if (tail !== null) segments.push(tail);
  return `VIX: the market's expected volatility over the next 30 days (Cboe). ${segments.join(' · ')}`;
}

/** Below 820 px the details move into the regime chip's tooltip. */
export function vixPhoneTitle(vix: VixPayload | null | undefined): string {
  const base = 'Regime from SPY';
  if (!vix || vix.close === null) return base;
  const parts = [`VIX ${fmtVixClose(vix.close)}`];
  const pct = pctNum(vix.percentile);
  if (pct !== null) parts.push(`${ordinal(pct)} percentile`);
  if (vix.date !== null) {
    parts.push(`close of ${fmtWeekdayDayShort(vix.date)}${vix.stale ? ' (stale)' : ''}`);
  } else if (vix.stale) {
    parts.push('stale close');
  }
  const since = fmtVixPct(vix.vixy_since_vix_pct);
  const today = fmtVixPct(vix.vixy_today_pct);
  const moves: string[] = [];
  if (since !== null) moves.push(`VIXY since ${since}`);
  if (today !== null) moves.push(`today ${today}`);
  const tail = moves.length ? ` · ${moves.join(', ')}` : '';
  return `${base} · ${parts.join(', ')}${tail}`;
}

/** The latest-decision card chip's hover text. */
export function vixCardTitle(vix: VixPayload): string {
  const segments: string[] = [];
  if (vix.stale) segments.push('stale — FRED has not posted a newer close');
  if (vix.date !== null) {
    segments.push(`close of ${fmtWeekdayDayShort(vix.date)} (FRED)`);
  }
  const chg = fmtVixPct(vix.change_5d_pct);
  if (chg !== null) segments.push(`5 sessions ${chg}`);
  const tail = vixyTail(vix, 'VIXY since that close');
  if (tail !== null) segments.push(tail);
  return `VIX at this cycle: ${segments.join(' · ')}`;
}
