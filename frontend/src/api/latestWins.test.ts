import { describe, expect, it } from 'vitest';

import { createSequence } from './latestWins';

describe('createSequence (latest request wins)', () => {
  it('the only claim stays current', () => {
    const seq = createSequence();
    const isCurrent = seq.next();
    expect(isCurrent()).toBe(true);
  });

  it('a new claim drops the older one — the race that matters: a slow first '
    + 'request resolves after a fast second and must be ignored', () => {
    const seq = createSequence();
    const first = seq.next();
    const second = seq.next();
    // The first response lands after the second was already issued.
    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });

  it('a stale claim never becomes current again, even when it resolves last', () => {
    const seq = createSequence();
    const a = seq.next();
    const b = seq.next();
    const c = seq.next();
    expect(c()).toBe(true);
    expect(b()).toBe(false);
    expect(a()).toBe(false);
    // And the winner stays the winner — a late older response cannot
    // reclaim the sequence.
    expect(c()).toBe(true);
  });
});
