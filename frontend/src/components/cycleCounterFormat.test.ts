import { describe, expect, it } from 'vitest';

import {
  checkLabel,
  counterCountdown,
  counterTail,
  nextCycleLabel,
  runLabel,
  todayLabel,
} from './cycleCounterFormat';
import type { CycleCounter } from '../types/api';

const counter = (over: Partial<CycleCounter>): CycleCounter => ({
  kill_switch: false,
  running: false,
  session_is_today: true,
  now: '2026-10-15T16:52:00Z',
  next_cycle: '2026-10-15T17:46:00Z', // 19:46 Amsterdam · 13:46 New York
  next_cycle_today: true,
  next_check: '2026-10-15T17:01:00Z', // 19:01 Amsterdam · 13:01 New York
  next_check_today: true,
  segments: [],
  cycle_index: 3,
  cycles_today: 6,
  run_cycle_number: 42,
  ...over,
});

describe('counterCountdown', () => {
  const now = new Date('2026-10-15T16:52:00Z').getTime();
  it('is mm:ss under an hour', () => {
    expect(counterCountdown('2026-10-15T17:46:00Z', now)).toBe('54:00');
    expect(counterCountdown('2026-10-15T16:59:07Z', now)).toBe('07:07');
  });
  it('is h:mm at an hour or more', () => {
    expect(counterCountdown('2026-10-15T18:06:00Z', now)).toBe('1:14');
    expect(counterCountdown('2026-10-15T20:52:00Z', now)).toBe('4:00');
  });
  it('floors at zero once the time has passed', () => {
    expect(counterCountdown('2026-10-15T16:00:00Z', now)).toBe('00:00');
  });
});

describe('nextCycleLabel', () => {
  it('shows Amsterdam and New York times for today', () => {
    expect(nextCycleLabel(counter({}))).toBe('next cycle 19:46 · 13:46 NY');
  });
  it('names the day when the next cycle is another session', () => {
    const c = counter({
      next_cycle: '2026-10-19T14:46:00Z', // Mon 16:46 Amsterdam · 10:46 NY
      next_cycle_today: false,
    });
    expect(nextCycleLabel(c)).toBe('next cycle Mon 16:46 · 10:46 NY');
  });
  it('says so when nothing is scheduled', () => {
    expect(nextCycleLabel(counter({ next_cycle: null }))).toBe('no cycle scheduled');
  });
});

describe('counterTail', () => {
  const now = new Date('2026-10-15T16:52:00Z').getTime();
  it('is "running" instead of the countdown while a cycle runs', () => {
    expect(counterTail(counter({ running: true }), now)).toBe('running');
  });
  it('is the live countdown otherwise', () => {
    expect(counterTail(counter({}), now)).toBe('in 54:00');
  });
  it('is empty when no cycle is scheduled', () => {
    expect(counterTail(counter({ next_cycle: null }), now)).toBeNull();
  });
  it('shows no countdown when the next cycle is over 24 hours away', () => {
    // Saturday → Monday's first cycle: the label already gives day + time.
    const c = counter({
      next_cycle: '2026-10-19T14:46:00Z', // Mon 10:46 NY
      next_cycle_today: false,
    });
    expect(counterTail(c, now)).toBeNull();
  });
});

describe('labels', () => {
  it('todayLabel is "Cycle n of 6 today"', () => {
    expect(todayLabel(counter({}))).toBe('Cycle 3 of 6 today');
    expect(todayLabel(counter({ cycle_index: 0 }))).toBe('Cycle 0 of 6 today');
  });
  it('todayLabel is empty on a day without a session', () => {
    expect(
      todayLabel(counter({ cycle_index: 0, cycles_today: 0, segments: [] })),
    ).toBeNull();
  });
  it('todayLabel drops "today" when the shown session is not today', () => {
    expect(
      todayLabel(counter({ session_is_today: false })),
    ).toBe('Cycle 3 of 6');
  });
  it('runLabel is the cycle number in this run', () => {
    expect(runLabel(counter({}))).toBe('#42 this run');
    expect(runLabel(counter({ run_cycle_number: null }))).toBeNull();
    expect(runLabel(counter({ run_cycle_number: 0 }))).toBeNull();
  });
  it('checkLabel is the next position check', () => {
    expect(checkLabel(counter({}))).toBe('next check 19:01');
    expect(
      checkLabel(
        counter({
          next_check: '2026-10-19T13:46:00Z',
          next_check_today: false,
        }),
      ),
    ).toBe('next check Mon 15:46');
    expect(checkLabel(counter({ next_check: null }))).toBeNull();
  });
});
