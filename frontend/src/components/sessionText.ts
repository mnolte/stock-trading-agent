import type { HeaderState } from '../types/api';
import { displayTime, displayWeekdayTime } from '../format';

/** "in 54 min" / "in 2 h 10 min" / "in 16 h 30 min" — server-clock based.
 * Past 24 hours the countdown is dropped: the target's day and time are
 * shown instead (docs/SPEC.md — Dashboard at scale and off-hours). */
export function countdown(fromIso: string, toIso: string): string {
  const mins = Math.round((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60_000);
  if (mins <= 0) return 'now';
  if (mins > 24 * 60) return '';
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `in ${h} h` : `in ${h} h ${m} min`;
}

export function sessionLabel(state: string): string {
  switch (state) {
    case 'open':
      return 'Market open';
    case 'last_cycle_done':
      return 'Last cycle done';
    case 'early_close':
      return 'Early close';
    case 'closed':
      return 'Market closed';
    case 'paused':
      return 'Paused';
    default:
      return state;
  }
}

/** The session pill text, from design/screens (States + Main). `nowIso`
 * defaults to the fetched server time; the header passes a skew-corrected
 * local now so the countdown ticks between refetches. */
export function sessionTail(
  state: string,
  session: HeaderState['session'],
  nowIso?: string,
): string {
  const now = nowIso ?? session.now;
  const check = session.next_check ? `next check ${displayTime(session.next_check)}` : null;

  switch (state) {
    case 'paused':
      return ' · no new entries · stops active';
    case 'open': {
      const parts: string[] = [];
      if (check) parts.push(check);
      if (session.next_cycle) {
        parts.push(`next cycle ${displayTime(session.next_cycle)}`);
        if (now) {
          const cd = countdown(now, session.next_cycle);
          if (cd) parts.push(cd);
        }
      }
      return parts.length ? ` · ${parts.join(' · ')}` : '';
    }
    case 'early_close': {
      const parts: string[] = [];
      if (check) parts.push(check);
      if (session.next_close) parts.push(`closes ${displayTime(session.next_close)}`);
      const n = session.cycles_today ?? 0;
      if (n > 0) parts.push(`${n} cycle${n === 1 ? '' : 's'} today`);
      return parts.length ? ` · ${parts.join(' · ')}` : '';
    }
    case 'last_cycle_done': {
      const parts: string[] = [];
      if (check) parts.push(check);
      if (session.next_close) parts.push(`closes ${displayTime(session.next_close)}`);
      parts.push('stops stay active');
      return ` · ${parts.join(' · ')}`;
    }
    case 'closed': {
      if (!session.next_open) return '';
      const open = `opens ${displayWeekdayTime(session.next_open)}`;
      const cd = now ? countdown(now, session.next_open) : '';
      return cd ? ` · ${open} · ${cd}` : ` · ${open}`;
    }
    default:
      return '';
  }
}
