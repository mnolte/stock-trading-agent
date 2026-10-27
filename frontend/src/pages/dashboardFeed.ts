import type { DashboardFeedItem } from '../types/api';

// Feed presentation helpers (design/screens/Main.dc.html, "Today's
// decisions"): decisions are grouped per cycle; changes (entries, exits,
// stop moves, approvals) always show as their own rows —
// everything else collapses into one row per (action, reason).

export interface FeedActionText {
  label: string;
  color: string;
}

/** Mono action label + colour. Every no-call row reads NO CALL — the
 * pre-filter marker is a reason, not an action. */
export function actionText(action: string): FeedActionText {
  switch (action) {
    case 'enter':
      return { label: 'ENTER', color: 'var(--sa-positive)' };
    case 'exit':
      return { label: 'EXIT', color: 'var(--sa-negative-text)' };
    case 'tighten_stop':
      return { label: 'TIGHTEN STOP', color: 'var(--sa-info)' };
    case 'blocked':
      return { label: 'BLOCKED', color: 'var(--sa-negative-text)' };
    case 'skip':
      return { label: 'SKIP', color: 'var(--sa-accent)' };
    case 'hold':
      return { label: 'HOLD', color: 'var(--sa-text-2)' };
    case 'no_call':
      return { label: 'NO CALL', color: 'var(--sa-text-muted)' };
    default:
      return {
        label: action.toUpperCase().replace(/_/g, ' '),
        color: 'var(--sa-text-2)',
      };
  }
}

export interface FeedRow {
  key: string;
  action: string;
  label: string;
  color: string;
  items: DashboardFeedItem[];
  reason: string | null;
}

export interface FeedCycle {
  key: string;
  /** Scheduled cycle time (UTC ISO). */
  time: string;
  /** 1-based cycle number within the session, when the backend knows it. */
  index: number | null;
  /** Decisions in this cycle. */
  total: number;
  /** Decisions that reached the model. */
  calls: number;
  /** All rows, ordered for the expanded view (changes first). */
  rows: FeedRow[];
  /** Only the change rows — what a collapsed cycle still shows. */
  changeRows: FeedRow[];
}

// Expanded-view row order: changes first, then held-position outcomes,
// then candidate outcomes, no-calls last (matches design's cycle order).
const ROW_ORDER: Record<string, number> = {
  enter: 0,
  exit: 1,
  tighten_stop: 2,
  blocked: 3,
  hold: 4,
  skip: 5,
  no_call: 6,
};

function toRow(
  action: string,
  reason: string | null,
  items: DashboardFeedItem[],
): FeedRow {
  const { label, color } = actionText(action);
  return {
    key: `${action}|${reason ?? ''}`,
    action,
    label,
    color,
    items,
    reason,
  };
}

function sortRows(rows: FeedRow[]): FeedRow[] {
  return [...rows].sort(
    (a, b) =>
      (ROW_ORDER[a.action] ?? 8) - (ROW_ORDER[b.action] ?? 8) ||
      a.items[0].symbol.localeCompare(b.items[0].symbol),
  );
}

/** Group a flat, newest-first feed into cycles (newest cycle first). */
export function groupFeed(items: DashboardFeedItem[]): FeedCycle[] {
  const byCycle = new Map<string, DashboardFeedItem[]>();
  for (const item of items) {
    const key = item.cycle_id != null ? `c${item.cycle_id}` : `t${item.time}`;
    const list = byCycle.get(key);
    if (list) list.push(item);
    else byCycle.set(key, [item]);
  }
  const cycles: FeedCycle[] = [...byCycle.entries()].map(([key, cycleItems]) => {
    const changes: FeedRow[] = [];
    const restGroups = new Map<string, DashboardFeedItem[]>();
    for (const item of cycleItems) {
      if (item.change) {
        changes.push(toRow(item.action, item.reason, [item]));
      } else {
        const key = `${item.action}|${item.reason ?? ''}`;
        const list = restGroups.get(key);
        if (list) list.push(item);
        else restGroups.set(key, [item]);
      }
    }
    const rest = [...restGroups.entries()].map(([key, groupItems]) => {
      const [action, reason] = key.split('|');
      return toRow(action, reason || null, groupItems);
    });
    return {
      key,
      time: cycleItems[0].time,
      index: cycleItems[0].cycle_index ?? null,
      total: cycleItems.length,
      calls: cycleItems.filter((i) => i.called).length,
      rows: sortRows([...changes, ...rest]),
      changeRows: sortRows(changes),
    };
  });
  // Items arrive newest-first; guard anyway so cycles order by time.
  cycles.sort((a, b) => b.time.localeCompare(a.time));
  return cycles;
}

/** "cycle 3 · 10 symbols · 5 model calls" — expanded header meta. */
export function cycleMeta(cycle: FeedCycle): string {
  const head = cycle.index !== null ? `cycle ${cycle.index}` : 'cycle';
  return `${head} · ${cycle.total} symbol${cycle.total === 1 ? '' : 's'} · ${cycle.calls} model call${cycle.calls === 1 ? '' : 's'}`;
}

/** "cycle 2 · 1 change · 9 more decisions" — collapsed header meta. */
export function collapsedMeta(cycle: FeedCycle): string {
  const head = cycle.index !== null ? `cycle ${cycle.index}` : 'cycle';
  const changes = cycle.changeRows.length;
  const rest = cycle.total - changes;
  if (changes === 0) {
    return `${head} · ${cycle.total} decision${cycle.total === 1 ? '' : 's'}`;
  }
  return `${head} · ${changes} change${changes === 1 ? '' : 's'} · ${rest} more decision${rest === 1 ? '' : 's'}`;
}

export const FEED_ROW_SYMBOL_LIMIT = 8;

/** "MSFT, JPM" with a "+3 more" marker past the display limit. */
export function rowSymbols(items: DashboardFeedItem[]): {
  text: string;
  more: number;
} {
  const symbols = items.map((i) => i.symbol);
  return {
    text: symbols.slice(0, FEED_ROW_SYMBOL_LIMIT).join(', '),
    more: Math.max(0, symbols.length - FEED_ROW_SYMBOL_LIMIT),
  };
}
