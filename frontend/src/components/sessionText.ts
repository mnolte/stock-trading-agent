import type { HeaderState } from '../types/api';
import { displayTime } from '../format';

/** "in 54 min" / "in 2 h 10 min" / "in 16 h 30 min" — server-clock based. */
export function countdown(fromIso: string, toIso: string): string {
  const mins = Math.round((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60_000);
  if (mins <= 0) return 'now';
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
    default:
      return state;
  }
}

/** The session pill text, from design/screens (States + Main). */
export function sessionTail(state: string, session: HeaderState['session']): string {
  const now = session.now;
  const target =
    state === 'closed'
      ? (session.next_open ?? null)
      : (session.next_cycle ?? session.next_close ?? null);
  if (!target) return '';
  const verb = state === 'closed' ? 'opens' : state === 'last_cycle_done' ? 'closes' : 'next cycle';
  const tail = `${verb} ${displayTime(target)}`;
  if (!now) return ` · ${tail}`;
  return ` · ${tail} · ${countdown(now, target)}`;
}
