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

/** True when the ISO timestamp falls on today's New York session date. */
export function openedTodayNy(iso: string): boolean {
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: MARKET_TZ };
  return new Date(iso).toLocaleDateString('en-CA', opts) === new Date().toLocaleDateString('en-CA', opts);
}
