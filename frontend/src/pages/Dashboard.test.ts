import { describe, expect, it } from 'vitest';

import {
  actionText,
  collapsedMeta,
  cycleMeta,
  groupFeed,
  rowSymbols,
} from './dashboardFeed';
import type { DashboardFeedItem } from '../types/api';

let nextId = 0;
const item = (over: Partial<DashboardFeedItem>): DashboardFeedItem => ({
  id: ++nextId,
  time: '2026-10-15T17:46:00Z',
  cycle_id: 1,
  cycle_index: 1,
  symbol: 'AAPL',
  context: 'entry',
  action: 'no_call',
  model_action: null,
  no_call_reason: null,
  reason: null,
  change: false,
  called: false,
  ...over,
});

describe('actionText', () => {
  it('labels every no-call NO CALL, pre-filter included', () => {
    expect(actionText('no_call').label).toBe('NO CALL');
  });
  it('keeps model actions unchanged', () => {
    expect(actionText('enter').label).toBe('ENTER');
    expect(actionText('skip').label).toBe('SKIP');
    expect(actionText('tighten_stop').label).toBe('TIGHTEN STOP');
    expect(actionText('deferred').label).toBe('DEFERRED');
    expect(actionText('exit').label).toBe('EXIT');
    expect(actionText('blocked').label).toBe('BLOCKED');
  });
});

describe('groupFeed', () => {
  it('groups decisions by cycle, newest first', () => {
    const cycles = groupFeed([
      item({ cycle_id: 2, cycle_index: 2, time: '2026-10-15T18:46:00Z' }),
      item({ cycle_id: 1, cycle_index: 1 }),
      item({ cycle_id: 2, cycle_index: 2, time: '2026-10-15T18:46:00Z', symbol: 'MSFT' }),
    ]);
    expect(cycles.map((c) => c.key)).toEqual(['c2', 'c1']);
    expect(cycles[0].total).toBe(2);
    expect(cycles[0].index).toBe(2);
  });

  it('keeps changes as their own rows and groups the rest by action + reason', () => {
    const cycles = groupFeed([
      item({ symbol: 'AAPL' }),
      item({ symbol: 'MSFT' }),
      item({ symbol: 'JPM', action: 'skip', reason: 'low confidence' }),
      item({ symbol: 'XOM', action: 'skip', reason: 'low confidence' }),
      item({ symbol: 'NVDA', action: 'enter', change: true, called: true }),
      item({ symbol: 'META', action: 'skip', reason: 'other' }),
    ]);
    const rows = cycles[0].rows;
    // enter change row + skip(low confidence) + skip(other) + no_call group
    expect(rows).toHaveLength(4);
    const enter = rows.find((r) => r.action === 'enter');
    expect(enter?.items).toHaveLength(1);
    const grouped = rows.find((r) => r.items.length === 2);
    expect(grouped?.action).toBe('skip');
    expect(grouped?.reason).toBe('low confidence');
    expect(cycles[0].changeRows).toHaveLength(1);
    expect(cycles[0].calls).toBe(1);
  });

  it('treats approvals as changes', () => {
    const cycles = groupFeed([
      item({ action: 'enter', change: true, reason: 'approval pending' }),
    ]);
    expect(cycles[0].changeRows).toHaveLength(1);
  });
});

describe('cycle meta', () => {
  it('counts symbols and calls when expanded', () => {
    const [cycle] = groupFeed([
      item({ called: true }),
      item({ symbol: 'MSFT', called: true }),
    ]);
    expect(cycleMeta(cycle)).toBe('cycle 1 · 2 symbols · 2 model calls');
  });
  it('summarises a collapsed cycle', () => {
    const [cycle] = groupFeed([
      item({ action: 'enter', change: true }),
      ...Array.from({ length: 9 }, (_, i) => item({ symbol: `S${i}` })),
    ]);
    expect(collapsedMeta(cycle)).toBe('cycle 1 · 1 change · 9 more decisions');
  });
  it('collapsed meta without changes counts decisions', () => {
    const [cycle] = groupFeed([item({}), item({ symbol: 'MSFT' })]);
    expect(collapsedMeta(cycle)).toBe('cycle 1 · 2 decisions');
  });
});

describe('rowSymbols', () => {
  it('caps the symbol list at 8 with a +n marker', () => {
    const items = Array.from({ length: 11 }, (_, i) =>
      item({ symbol: `S${i}` }),
    );
    const { text, more } = rowSymbols(items);
    expect(text).toBe('S0, S1, S2, S3, S4, S5, S6, S7');
    expect(more).toBe(3);
  });
});
