import { describe, expect, it } from 'vitest';

import { countdown, sessionTail } from './sessionText';
import type { HeaderState } from '../types/api';

const session = (over: Partial<HeaderState['session']>): HeaderState['session'] => ({
  state: 'open',
  display_time: '',
  now: '2026-10-15T16:52:00+00:00',
  next_cycle: null,
  next_open: null,
  next_close: null,
  ...over,
});

describe('countdown', () => {
  it('shows minutes under an hour', () => {
    expect(countdown('2026-10-15T16:52:00Z', '2026-10-15T17:46:00Z')).toBe('in 54 min');
  });
  it('shows hours and minutes', () => {
    expect(countdown('2026-10-15T16:52:00Z', '2026-10-16T09:22:00Z')).toBe('in 16 h 30 min');
  });
  it('rounds whole hours without minutes', () => {
    expect(countdown('2026-10-15T16:52:00Z', '2026-10-15T19:52:00Z')).toBe('in 3 h');
  });
});

describe('sessionTail', () => {
  it('open: next cycle + countdown', () => {
    const tail = sessionTail('open', session({ next_cycle: '2026-10-15T17:46:00Z' }));
    expect(tail).toBe(' · next cycle 19:46 · in 54 min');
  });
  it('open: shows the next position check alongside the next cycle', () => {
    const tail = sessionTail(
      'open',
      session({ next_check: '2026-10-15T17:01:00Z', next_cycle: '2026-10-15T17:46:00Z' }),
    );
    expect(tail).toBe(' · next check 19:01 · next cycle 19:46 · in 54 min');
  });
  it('last cycle done: closes and stops stay active', () => {
    const tail = sessionTail(
      'last_cycle_done',
      session({ state: 'last_cycle_done', next_close: '2026-10-15T20:00:00Z' }),
    );
    expect(tail).toBe(' · closes 22:00 · stops stay active');
  });
  it('closed: opens with weekday and countdown', () => {
    const tail = sessionTail(
      'closed',
      session({ state: 'closed', next_open: '2026-10-16T13:30:00Z' }),
    );
    expect(tail).toBe(' · opens Fri 15:30 · in 20 h 38 min');
  });
  it('early close: closes early with the cycles that still run', () => {
    const tail = sessionTail(
      'early_close',
      session({ state: 'early_close', next_close: '2026-11-27T18:00:00Z', cycles_today: 3 }),
    );
    expect(tail).toBe(' · closes 19:00 · 3 cycles today');
  });
  it('early close: includes the next check before the close', () => {
    const tail = sessionTail(
      'early_close',
      session({
        state: 'early_close',
        next_check: '2026-11-27T16:31:00Z',
        next_close: '2026-11-27T18:00:00Z',
        cycles_today: 3,
      }),
    );
    expect(tail).toBe(' · next check 17:31 · closes 19:00 · 3 cycles today');
  });
});
