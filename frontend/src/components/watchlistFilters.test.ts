import { describe, expect, it } from 'vitest';

import type { WatchlistRow } from '../types/api';
import {
  filterCounts,
  filterRows,
  searchRows,
  sortByRank,
} from './watchlistFilters';

function row(
  symbol: string,
  status: 'pass' | 'fail' | 'held',
  extra: Partial<WatchlistRow> = {},
): WatchlistRow {
  return {
    symbol,
    price: '225.10',
    day_change_pct: 0.21,
    rules: { status, reason: null, checks: [] },
    earnings_date: null,
    last_action: null,
    last_action_time: null,
    ...extra,
  };
}

const ROWS = [
  row('AAPL', 'pass', { rank: 3 }),
  row('MSFT', 'held'),
  row('JPM', 'fail', { rank: 5, days_to_earnings: 2 }),
  row('XOM', 'pass', { rank: 1 }),
  row('SPY', 'fail', { days_to_earnings: 10 }),
];

describe('filterRows', () => {
  it('returns all rows for the all tab', () => {
    expect(filterRows(ROWS, 'all', 3)).toHaveLength(5);
  });

  it('pass shows only rules-passing symbols', () => {
    expect(filterRows(ROWS, 'pass', 3).map((r) => r.symbol)).toEqual([
      'AAPL',
      'XOM',
    ]);
  });

  it('held shows only held symbols', () => {
    expect(filterRows(ROWS, 'held', 3).map((r) => r.symbol)).toEqual(['MSFT']);
  });

  it('earnings keeps rows within the blackout window only', () => {
    expect(filterRows(ROWS, 'earnings', 3).map((r) => r.symbol)).toEqual([
      'JPM',
    ]);
    expect(filterRows(ROWS, 'earnings', 12).map((r) => r.symbol)).toEqual([
      'JPM',
      'SPY',
    ]);
  });

  it('counts match the filters', () => {
    expect(filterCounts(ROWS, 3)).toEqual({
      all: 5,
      pass: 2,
      held: 1,
      earnings: 1,
    });
  });
});

describe('sortByRank', () => {
  it('orders by rank; unranked rows come last by symbol', () => {
    expect(sortByRank(ROWS).map((r) => r.symbol)).toEqual([
      'XOM',
      'AAPL',
      'JPM',
      'MSFT',
      'SPY',
    ]);
  });
});

describe('searchRows', () => {
  it('matches a case-insensitive substring', () => {
    expect(searchRows(ROWS, 'a').map((r) => r.symbol)).toEqual(['AAPL']);
    expect(searchRows(ROWS, '  spy ').map((r) => r.symbol)).toEqual(['SPY']);
  });

  it('empty query returns everything', () => {
    expect(searchRows(ROWS, '')).toHaveLength(5);
    expect(searchRows(ROWS, '   ')).toHaveLength(5);
  });
});
