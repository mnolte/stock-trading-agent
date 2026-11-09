/** Display formatting: times are shown in Amsterdam time per the spec. */

const DISPLAY_TZ = 'Europe/Amsterdam';
const MARKET_TZ = 'America/New_York';

// ---------------------------------------------------------------------------
// Dates (056): fixed English month names, never the browser locale.
// "14 Sep" inside the current year, "14 Sep 2025" otherwise; ranges
// "14–25 Sep 2026" / "28 Sep – 2 Oct". ISO stays in Logs, exports, URLs.

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

export interface DayParts {
  y: number;
  m: number; // 1-12
  d: number;
}

function partsInTz(
  iso: string,
  timeZone: string,
): { y: number; m: number; d: number } | null {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value;
  const y = get('year');
  const m = get('month');
  const d = get('day');
  if (!y || !m || !d) return null;
  return { y: +y, m: +m, d: +d };
}

/** Calendar parts of an ISO timestamp (New York date) or "YYYY-MM-DD". */
export function dayParts(
  iso: string | null | undefined,
  timeZone: string = MARKET_TZ,
): DayParts | null {
  if (!iso) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (dateOnly) {
    return { y: +dateOnly[1], m: +dateOnly[2], d: +dateOnly[3] };
  }
  return partsInTz(iso, timeZone);
}

/** "16 Oct" — day + fixed English month in the given zone (charts, axes). */
export function fmtDayMonthIn(
  iso: string,
  timeZone: string = DISPLAY_TZ,
): string {
  const p = dayParts(iso, timeZone);
  return p ? `${p.d} ${MONTHS[p.m - 1]}` : '';
}

function weekdayIn(iso: string, timeZone: string, long: boolean): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone,
    weekday: long ? 'long' : 'short',
  }).format(new Date(iso));
}

function currentYear(): number {
  return dayParts(new Date().toISOString())!.y;
}

function fmtDayMonth(p: DayParts): string {
  return `${p.d} ${MONTHS[p.m - 1]}`;
}

/** "14 Sep" this year, "14 Sep 2025" otherwise. */
export function fmtDay(iso: string | null | undefined): string {
  const p = dayParts(iso);
  if (!p) return '—';
  const base = fmtDayMonth(p);
  return p.y === currentYear() ? base : `${base} ${p.y}`;
}

/** "Sep 2025" — month + year (knowledge-cutoff notes). */
export function fmtMonthYear(iso: string | null | undefined): string {
  const p = dayParts(iso);
  return p ? `${MONTHS[p.m - 1]} ${p.y}` : '—';
}

/** "14 Sep 2025" — always with the year (table columns, exports-adjacent UI). */
export function fmtDayYear(iso: string | null | undefined): string {
  const p = dayParts(iso);
  return p ? `${fmtDayMonth(p)} ${p.y}` : '—';
}

/** "Friday 14 Sep" / "Friday 14 Sep 2025" — spelled-out weekday + date. */
export function fmtWeekdayDay(iso: string | null | undefined): string {
  const p = dayParts(iso);
  if (!p) return '—';
  const weekday = new Intl.DateTimeFormat('en-US', {
    timeZone: MARKET_TZ,
    weekday: 'long',
  }).format(new Date(iso!.includes('T') ? iso! : `${iso}T12:00:00Z`));
  const base = `${weekday} ${fmtDayMonth(p)}`;
  return p.y === currentYear() ? base : `${base} ${p.y}`;
}

/** Date range: "14–25 Sep 2026", "28 Sep – 2 Oct", "28 Dec 2025 – 2 Jan 2026". */
export function fmtRange(
  fromIso: string | null | undefined,
  toIso: string | null | undefined,
): string {
  const a = dayParts(fromIso);
  const b = dayParts(toIso);
  if (!a || !b) return '—';
  const thisYear = a.y === currentYear() && b.y === currentYear();
  const yearSuffix = (p: DayParts) => (thisYear ? '' : ` ${p.y}`);
  if (a.y === b.y && a.m === b.m) {
    return `${a.d}–${b.d} ${MONTHS[a.m - 1]}${yearSuffix(a)}`;
  }
  if (a.y === b.y) {
    return `${fmtDayMonth(a)} – ${fmtDayMonth(b)}${yearSuffix(a)}`;
  }
  return `${fmtDayMonth(a)} ${a.y} – ${fmtDayMonth(b)} ${b.y}`;
}

const MONTH_LOOKUP: Record<string, number> = Object.fromEntries(
  MONTHS.map((name, i) => [name.toLowerCase(), i + 1]),
);

/** Parse a typed date — "14 Sep", "14 Sep 2025", "14-09-2025", "2026-09-14"
 * — into "YYYY-MM-DD", or null when it doesn't read as a date. */
export function parseDayInput(text: string): string | null {
  const t = text.trim();
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t);
  if (m) return isoDay(+m[1], +m[2], +m[3]);
  m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(t);
  if (m) return isoDay(+m[3], +m[2], +m[1]);
  m = /^(\d{1,2})\s+([A-Za-z]{3,})\.?,?\s*(\d{4})?$/.exec(t);
  if (!m) return null;
  const mon = MONTH_LOOKUP[m[2].slice(0, 3).toLowerCase()];
  if (!mon) return null;
  const y = m[3] ? +m[3] : currentYear();
  return isoDay(y, mon, +m[1]);
}

function isoDay(y: number, m: number, d: number): string | null {
  if (m < 1 || m > 12 || d < 1 || d > 31 || y < 1900) return null;
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Timestamp displays (Amsterdam time, New York on hover).

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
  const p = dayParts(iso, DISPLAY_TZ);
  const date = p ? `${p.d} ${MONTHS[p.m - 1]}` : '';
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
  const p = dayParts(iso, DISPLAY_TZ);
  const date = p ? `${p.d} ${MONTHS[p.m - 1]}` : '';
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${date} ${time}`;
}

/** "Friday 16 October" — long weekday + month in Amsterdam time. */
export function displayFullDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('en-GB', {
    weekday: 'long',
    timeZone: DISPLAY_TZ,
  });
  const p = dayParts(iso, DISPLAY_TZ);
  const month = p
    ? new Intl.DateTimeFormat('en-US', {
        timeZone: DISPLAY_TZ,
        month: 'long',
      }).format(d)
    : '';
  return `${weekday} ${p!.d} ${month}`;
}

/** "Fri 15:30" — weekday + time in Amsterdam time (design's "opens Fri 15:30"). */
export function displayWeekdayTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const weekday = weekdayIn(iso, DISPLAY_TZ, false);
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
  const weekday = weekdayIn(iso, DISPLAY_TZ, false);
  const p = dayParts(iso, DISPLAY_TZ);
  const date = p ? `${p.d} ${MONTHS[p.m - 1]}` : '';
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${weekday} ${date} ${time}`;
}

/** "Friday 23 Oct" — a session date ("2026-10-23") spelled out. */
export function sessionDayLabel(isoDate: string | null | undefined): string {
  if (!isoDate) return '';
  const p = dayParts(isoDate);
  if (!p) return '';
  const weekday = weekdayIn(`${isoDate}T12:00:00Z`, 'UTC', true);
  const base = `${weekday} ${fmtDayMonth(p)}`;
  return p.y === currentYear() ? base : `${base} ${p.y}`;
}

/** "Fri 23 Oct" — short session date (tile labels, chips). */
export function sessionDayShort(isoDate: string | null | undefined): string {
  if (!isoDate) return '';
  const p = dayParts(isoDate);
  if (!p) return '';
  const weekday = weekdayIn(`${isoDate}T12:00:00Z`, 'UTC', false);
  const base = `${weekday} ${fmtDayMonth(p)}`;
  return p.y === currentYear() ? base : `${base} ${p.y}`;
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

/** "6 h 0 min" — the stop-missing chip's elapsed time (item 050). */
export function elapsedHm(iso: string): string {
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  return `${Math.floor(mins / 60)} h ${mins % 60} min`;
}

/** True when the ISO timestamp falls on today's New York session date. */
export function openedTodayNy(iso: string): boolean {
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: MARKET_TZ };
  return new Date(iso).toLocaleDateString('en-CA', opts) === new Date().toLocaleDateString('en-CA', opts);
}
