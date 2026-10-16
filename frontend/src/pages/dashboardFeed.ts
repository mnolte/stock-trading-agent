import type { DashboardFeedItem } from '../types/api';

export type FeedChip = { label: string; variant: 'positive' | 'warning' | 'negative' | 'info' | 'neutral' };

export const FEED_CHIP: Record<string, FeedChip> = {
  enter: { label: 'ENTER', variant: 'positive' },
  skip: { label: 'SKIP', variant: 'warning' },
  hold: { label: 'HOLD', variant: 'neutral' },
  blocked: { label: 'BLOCKED', variant: 'negative' },
  exit: { label: 'EXIT', variant: 'negative' },
  tighten_stop: { label: 'TIGHTEN STOP', variant: 'info' },
  deferred: { label: 'DEFERRED', variant: 'warning' },
};

/** Chip for a stored action; `no_call` splits into RULES vs NO CALL by reason. */
export function actionChip(action: string, noCallReason: string | null): FeedChip {
  if (action === 'no_call') {
    return noCallReason === 'rules' || noCallReason === null
      ? { label: 'RULES', variant: 'neutral' }
      : { label: 'NO CALL', variant: 'neutral' };
  }
  return FEED_CHIP[action] ?? { label: action.toUpperCase().replace(/_/g, ' '), variant: 'neutral' };
}

export function feedText(item: DashboardFeedItem): string {
  if (item.action === 'no_call') {
    if (item.no_call_reason === 'rules' || item.no_call_reason === null) {
      return `${item.symbol} held by pre-filter`;
    }
    return `${item.symbol} · ${(item.reason ?? item.no_call_reason).replace(/_/g, ' ')}`;
  }
  const reason = item.reason ?? '';
  return `${item.symbol}${reason ? ` · ${reason}` : ''}`;
}
