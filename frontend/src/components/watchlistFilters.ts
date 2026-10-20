import type { WatchlistRow } from '../types/api';

// Watchlist filter tabs (design/screens/Main.dc.html): All, Pass rules,
// Held, Earnings ≤ N days — N is the configured earnings blackout window.

export type WatchlistFilter = 'all' | 'pass' | 'held' | 'earnings';

export function filterRows(
  rows: WatchlistRow[],
  filter: WatchlistFilter,
  earningsDays: number,
): WatchlistRow[] {
  switch (filter) {
    case 'pass':
      return rows.filter((r) => r.rules.status === 'pass');
    case 'held':
      return rows.filter((r) => r.rules.status === 'held');
    case 'earnings':
      return rows.filter(
        (r) =>
          r.days_to_earnings !== null &&
          r.days_to_earnings !== undefined &&
          r.days_to_earnings <= earningsDays,
      );
    default:
      return rows;
  }
}

export function filterCounts(
  rows: WatchlistRow[],
  earningsDays: number,
): Record<WatchlistFilter, number> {
  return {
    all: rows.length,
    pass: filterRows(rows, 'pass', earningsDays).length,
    held: filterRows(rows, 'held', earningsDays).length,
    earnings: filterRows(rows, 'earnings', earningsDays).length,
  };
}

// Screened mode: the eligible list is ranked by the daily screen; rows
// without a rank (held extras, SPY off-list) come last, then by symbol.
export function sortByRank(rows: WatchlistRow[]): WatchlistRow[] {
  return [...rows].sort(
    (a, b) =>
      (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER) ||
      a.symbol.localeCompare(b.symbol),
  );
}

export function searchRows(rows: WatchlistRow[], query: string): WatchlistRow[] {
  const q = query.trim().toUpperCase();
  if (!q) return rows;
  return rows.filter((r) => r.symbol.includes(q));
}
