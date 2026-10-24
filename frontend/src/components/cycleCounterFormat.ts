import type { CycleCounter, CycleCounterSegment } from '../types/api';
import { displayTime, displayWeekdayTime, nyTime } from '../format';

// Display formatting for the dashboard cycle counter. Every schedule
// fact (cycle times, segments, session boundaries) comes from
// /api/dashboard's cycle_counter — nothing here computes the schedule.
// Text follows design/screens/Main.dc.html and docs/SPEC.md ("Cycle
// counter").

/** Live countdown: "54:12" under an hour, "1:14" at or above. */
export function counterCountdown(targetIso: string, nowMs: number): string {
  const total = Math.max(0, Math.round((new Date(targetIso).getTime() - nowMs) / 1000));
  if (total < 3600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return `${h}:${String(m).padStart(2, '0')}`;
}

/** "next cycle 19:46 · 13:46 NY"; with the weekday when the next cycle is
 * another session ("next cycle Mon 16:46 · 10:46 NY", market closed). */
export function nextCycleLabel(c: CycleCounter): string {
  if (!c.next_cycle) return 'no cycle scheduled';
  const when = c.next_cycle_today
    ? displayTime(c.next_cycle)
    : displayWeekdayTime(c.next_cycle);
  return `next cycle ${when} · ${nyTime(c.next_cycle)} NY`;
}

/** "in 54:12" while waiting, "running" while a cycle runs. A countdown
 * longer than 24 hours shows only the day and time instead
 * (docs/SPEC.md — the label already carries "Mon 16:46 · 10:46 NY"). */
export function counterTail(c: CycleCounter, nowMs: number): string | null {
  if (c.running) return 'running';
  if (!c.next_cycle) return null;
  const gap = new Date(c.next_cycle).getTime() - nowMs;
  if (gap > 24 * 60 * 60 * 1000) return null;
  return `in ${counterCountdown(c.next_cycle, nowMs)}`;
}

/** "Cycle 3 of 6 today" — the count of the session's cycles that have run.
 * Off-hours the shown session is the last completed one, so the "today"
 * suffix drops off. Null when there is no shown session. */
export function todayLabel(c: CycleCounter): string | null {
  if (!c.cycles_today) return null;
  return `Cycle ${c.cycle_index} of ${c.cycles_today}${
    c.session_is_today ? ' today' : ''
  }`;
}

/** "#42 this run" — cycles since the run started; hidden until the
 * first cycle of the run (and when there is no run yet). */
export function runLabel(c: CycleCounter): string | null {
  return c.run_cycle_number ? `#${c.run_cycle_number} this run` : null;
}

/** "next check 20:01", with the weekday when it is another session. */
export function checkLabel(c: CycleCounter): string | null {
  if (!c.next_check) return null;
  const when = c.next_check_today
    ? displayTime(c.next_check)
    : displayWeekdayTime(c.next_check);
  return `next check ${when}`;
}

/** Hover text for one session segment. */
export function segmentTitle(s: CycleCounterSegment): string {
  const state =
    s.status === 'done' ? 'done' : s.status === 'next' ? 'next up' : 'later';
  return `${displayTime(s.time)} Amsterdam · ${nyTime(s.time)} New York — ${state}`;
}
