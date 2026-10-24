/** Display formatting: times are shown in Amsterdam time per the spec. */

const DISPLAY_TZ = 'Europe/Amsterdam';
const MARKET_TZ = 'America/New_York';

export function displayTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
}

/** "16 Oct 20:35" — compact date + time in Amsterdam time. */
export function displayDayTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: DISPLAY_TZ });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${date} ${time}`;
}

export function displayDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: DISPLAY_TZ });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${date} ${time}`;
}

/** "Friday 16 October" — long weekday + date in Amsterdam time. */
export function displayFullDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    timeZone: DISPLAY_TZ,
  });
}

/** "Fri 15:30" — weekday + time in Amsterdam time (design's "opens Fri 15:30"). */
export function displayWeekdayTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('en-GB', { weekday: 'short', timeZone: DISPLAY_TZ });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${weekday} ${time}`;
}

/** "12:52" in New York time — the market clock, shown on hover. */
export function nyTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: MARKET_TZ,
  });
}

/** "Fri 23 Oct 21:46" — weekday + date + time in Amsterdam time. Used when a
 * shown timestamp is not from today (e.g. the last session's decision). */
export function displayWeekdayDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const date = d.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${date} ${time}`;
}

/** "Friday 23 Oct" — a session date ("2026-10-23") spelled out. */
export function sessionDayLabel(isoDate: string | null | undefined): string {
  if (!isoDate) return '';
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** "Fri 23 Oct" — short session date (tile labels, chips). */
export function sessionDayShort(isoDate: string | null | undefined): string {
  if (!isoDate) return '';
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** "+1.23" / "−1.23" — signed number with the real minus sign (U+2212,
 * design/DESIGN.md numbers rule). Zero has no sign. */
export function signed(n: number, digits = 2): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(digits)}`;
}

/** "+1.23%" / "−1.23%". */
export function signedPct(n: number, digits = 2): string {
  return `${signed(n, digits)}%`;
}

/** "+$12.40" / "−$12.40". */
export function signedUsd(n: number, digits = 2): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toFixed(digits)}`;
}

/** True when the ISO timestamp falls on today's New York session date. */
export function openedTodayNy(iso: string): boolean {
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: MARKET_TZ };
  return new Date(iso).toLocaleDateString('en-CA', opts) === new Date().toLocaleDateString('en-CA', opts);
}
