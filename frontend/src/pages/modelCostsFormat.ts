/** Cost per session chart — types, constants and pure helpers
 * (item 112, docs/SPEC.md → Model costs → Cost per session). Kept
 * component-free so the unit tests and fast refresh stay clean. */

export interface SessionBucket {
  key: string;
  label: string;
  from: string;
  to: string;
  sessions: number;
  live: string;
  shadows: string;
  reviews: string;
  ask: string;
}

export interface SessionSummary {
  average_per_session: string | null;
  highest: {
    key: string;
    total: string;
    largest_part: string | null;
  } | null;
}

export type SessionUnit = 'session' | 'week' | 'month' | 'quarter';

// Chart constants (item 112): the 31-bar cap itself lives in the
// backend's bucket_unit; these are the frontend's fit rules.
export const PLOT_HEIGHT = 160;
export const LABEL_MIN_GAP = 36;

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const MONTHS_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

export function fmtUsd(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  if (Math.abs(n) > 0 && Math.abs(n) < 0.005) return '<$0.01';
  return `$${n.toFixed(2)}`;
}

function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

/** "Mon 14 Dec" — the sentence's and aria labels' date form. */
export function fmtSessionDay(iso: string): string {
  const d = parseDay(iso);
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}

/** "19–23 Oct" inside a month, "28 Dec 2026 – 1 Jan 2027" across years. */
export function fmtDateRange(fromIso: string, toIso: string): string {
  const f = parseDay(fromIso);
  const t = parseDay(toIso);
  const fy = f.getUTCFullYear();
  const ty = t.getUTCFullYear();
  const sameMonth = f.getUTCMonth() === t.getUTCMonth() && fy === ty;
  if (sameMonth) {
    return f.getUTCDate() === t.getUTCDate()
      ? `${t.getUTCDate()} ${MONTHS_SHORT[t.getUTCMonth()]}`
      : `${f.getUTCDate()}–${t.getUTCDate()} ${MONTHS_SHORT[t.getUTCMonth()]}`;
  }
  const left = `${f.getUTCDate()} ${MONTHS_SHORT[f.getUTCMonth()]}${fy !== ty ? ` ${fy}` : ''}`;
  const right = `${t.getUTCDate()} ${MONTHS_SHORT[t.getUTCMonth()]}${fy !== ty ? ` ${ty}` : ''}`;
  return `${left} – ${right}`;
}

/** The dates a bucket covers, for the detail line and tooltips. */
export function bucketDates(row: SessionBucket, unit: SessionUnit): string {
  if (unit === 'session') return fmtSessionDay(row.from);
  if (unit === 'week') return `${row.label} · ${fmtDateRange(row.from, row.to)}`;
  if (unit === 'month') {
    const d = parseDay(row.from);
    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  return `${row.label} ${parseDay(row.from).getUTCFullYear()}`;
}

/** Distance between the centres of two neighbouring bar labels. */
export function labelPitch(plotWidth: number, count: number, gap: number): number {
  if (count <= 0 || plotWidth <= 0) return Number.POSITIVE_INFINITY;
  return (plotWidth + gap) / count;
}

/** Which bar labels show: the first and the last always, the rest only
 * when at least LABEL_MIN_GAP px from the last shown one. */
export function thinnedLabels(count: number, pitch: number): boolean[] {
  const show = new Array<boolean>(count).fill(false);
  if (count === 0) return show;
  show[0] = true;
  let last = 0;
  for (let i = 1; i < count; i++) {
    if (i === count - 1 || (i - last) * pitch >= LABEL_MIN_GAP) {
      show[i] = true;
      last = i;
    }
  }
  return show;
}

/** "1–18 December" for the chart group's aria-label. */
export function spanLabel(rows: SessionBucket[]): string {
  if (rows.length === 0) return '';
  const f = parseDay(rows[0].from);
  const t = parseDay(rows[rows.length - 1].to);
  if (
    f.getUTCFullYear() === t.getUTCFullYear() &&
    f.getUTCMonth() === t.getUTCMonth()
  ) {
    return f.getUTCDate() === t.getUTCDate()
      ? `${f.getUTCDate()} ${MONTHS[f.getUTCMonth()]}`
      : `${f.getUTCDate()}–${t.getUTCDate()} ${MONTHS[f.getUTCMonth()]}`;
  }
  return fmtDateRange(rows[0].from, rows[rows.length - 1].to);
}

const PART_LABELS: Record<string, string> = {
  live: 'Live decisions',
  shadows: 'Shadows',
  reviews: 'Reviews',
  ask: 'Ask',
};

/** The fixed-template answer sentence above the chart (spec → Cost per
 * session). The average is always per session; the highest clause names
 * the bucket's dates and its largest part. */
export function sessionSentence(
  unit: SessionUnit,
  rows: SessionBucket[],
  summary: SessionSummary | null,
): string {
  const parts: string[] = [];
  const avg = summary?.average_per_session;
  if (avg != null) parts.push(`Average ${fmtUsd(avg)} a session`);
  const hi = summary?.highest;
  if (hi) {
    const row = rows.find((r) => r.key === hi.key);
    const d = row ? parseDay(row.from) : null;
    let clause: string;
    if (unit === 'session' && row) {
      clause = `the highest was ${fmtUsd(hi.total)} on ${fmtSessionDay(row.from)}`;
    } else if (unit === 'week' && row) {
      clause = `the highest week was ${row.label} (${fmtDateRange(row.from, row.to)})`;
    } else if (unit === 'month' && row && d) {
      clause = `the highest month was ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    } else if (unit === 'quarter' && row && d) {
      clause = `the highest quarter was ${row.label} ${d.getUTCFullYear()}`;
    } else {
      clause = `the highest was ${fmtUsd(hi.total)}`;
    }
    const part = hi.largest_part ? PART_LABELS[hi.largest_part] : null;
    if (part) clause += `, with ${part} the largest part`;
    parts.push(clause);
  }
  return parts.length ? `${parts.join('; ')}.` : '';
}
