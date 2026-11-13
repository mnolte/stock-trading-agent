import type {
  UniverseRow,
  UniverseSyncJob,
  UniverseSyncResponse,
} from '../types/api';
import { sessionDayShort } from '../format';

/** Parse a sync timestamp (naive UTC + "Z", or already zoned) to ms. */
export function isoMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = new Date(
    iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`,
  ).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** "18:46" Amsterdam for an ISO timestamp (sync rows are UTC). */
export function amsTime(iso: string | null | undefined): string {
  const ms = isoMs(iso);
  if (ms === null) return '—';
  return new Date(ms).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

/** "38 s", "1.2 s", "3 min", "1 h 5 min" — sync durations and ETA. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return '—';
  }
  if (seconds < 60) {
    const s = seconds < 10 ? seconds.toFixed(1) : Math.round(seconds).toString();
    return `${s} s`;
  }
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** The chip text for a pool row's one effective status (item 077). */
export function statusWord(row: UniverseRow): string {
  if (row.status === 'eligible_fails') {
    return `eligible · ${row.fails} fail${row.fails === 1 ? '' : 's'}`;
  }
  if (row.status === 'eligible' && row.fails > 0) {
    return `eligible · ${row.fails} fail${row.fails === 1 ? '' : 's'}`;
  }
  return row.status.replace('_', ' ');
}

/** The line under the chip for a row that kept its place with a failed
 * history check (item 093): the screen's numbers and the current ones.
 * Status, rank and fail count stay the screen's record — only a screen
 * changes them. Null for every other row. */
export function historyNowText(row: UniverseRow): string | null {
  const now = row.history_now;
  if (!now) return null;
  const screen = `Screen of ${sessionDayShort(row.screen_date)}: ${row.detail}`;
  if (now.loaded) {
    return `${screen}. Now: history loaded. The fail clears at the next passing screen.`;
  }
  return `${screen}. Now: ${now.detail}.`;
}

/** Counts that decide whether the Data sync card needs attention. */
export function syncAttention(sync: UniverseSyncResponse): {
  failed: number;
  running: number;
  late: number;
} {
  let failed = 0;
  let running = 0;
  let late = 0;
  for (const j of sync.jobs) {
    if (j.status === 'running') running += 1;
    else if (j.status === 'failed' || j.status === 'partial') failed += 1;
    if (j.late) late += 1;
  }
  return { failed, running, late };
}

/** The result line of one Data sync job row — written by the job, never
 * guessed: done of total, then each failure with its plain-words error
 * and retry time (docs/SPEC.md, Universe screen → Data sync). */
export function jobResultText(
  job: UniverseSyncJob,
  sync: UniverseSyncResponse,
): string {
  if (job.job === 'history' && job.status === 'running' && sync.history) {
    const h = sync.history;
    const eta =
      h.eta_seconds !== null ? ` · about ${fmtDuration(h.eta_seconds)} left` : '';
    return `Loading history · ${h.ready} of ${h.total ?? '?'} symbols ready${eta}`;
  }
  const parts: string[] = [];
  if (job.total !== null && job.total > 0 && job.done !== null) {
    const unit = job.job === 'screen' ? 'checked' : 'symbols';
    parts.push(`${job.done} of ${job.total} ${unit}`);
  }
  for (const f of job.failures.slice(0, 2)) {
    let text = `${f.what}: ${f.error}`;
    if (f.next_retry) {
      const retry = new Date(
        f.next_retry.endsWith('Z') ? f.next_retry : `${f.next_retry}Z`,
      );
      if (!Number.isNaN(retry.getTime())) {
        const hm = retry.toLocaleTimeString('en-GB', {
          hour: '2-digit',
          minute: '2-digit',
          timeZone: 'America/New_York',
        });
        const day = retry.toLocaleDateString('en-US', {
          weekday: 'short',
          timeZone: 'America/New_York',
        });
        text += ` · retry ${hm} ${day}`;
      }
    }
    parts.push(text);
  }
  if (job.failures.length > 2) parts.push(`+${job.failures.length - 2} more`);
  if (parts.length === 0) {
    parts.push(job.status === 'running' ? 'running' : job.status);
  }
  return parts.join(' · ');
}
