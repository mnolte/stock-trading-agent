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

/** True when the ISO timestamp falls on today's New York session date. */
export function openedTodayNy(iso: string): boolean {
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: MARKET_TZ };
  return new Date(iso).toLocaleDateString('en-CA', opts) === new Date().toLocaleDateString('en-CA', opts);
}
