import { describe, expect, it } from 'vitest';

import { actionChip, feedText } from './dashboardFeed';
import type { DashboardFeedItem } from '../types/api';

const item = (over: Partial<DashboardFeedItem>): DashboardFeedItem => ({
  id: 1,
  time: '2026-10-15T17:46:00Z',
  symbol: 'AAPL',
  context: 'entry',
  action: 'no_call',
  model_action: null,
  no_call_reason: null,
  reason: null,
  ...over,
});

describe('actionChip', () => {
  it('labels a rules pre-filter hold as RULES', () => {
    expect(actionChip('no_call', 'rules').label).toBe('RULES');
    expect(actionChip('no_call', null).label).toBe('RULES');
  });
  it('labels non-rules no-calls as NO CALL', () => {
    for (const reason of ['call_cap', 'price_stale', 'model_error']) {
      expect(actionChip('no_call', reason).label).toBe('NO CALL');
    }
  });
  it('keeps model actions unchanged', () => {
    expect(actionChip('enter', null).label).toBe('ENTER');
    expect(actionChip('skip', null).label).toBe('SKIP');
    expect(actionChip('deferred', null).label).toBe('DEFERRED');
  });
});

describe('feedText', () => {
  it('describes a rules hold as held by the pre-filter', () => {
    expect(feedText(item({ no_call_reason: 'rules' }))).toBe('AAPL held by pre-filter');
  });
  it('shows the stored reason for other no-calls', () => {
    expect(feedText(item({ no_call_reason: 'model_error', reason: 'timeout' }))).toBe('AAPL · timeout');
    expect(feedText(item({ no_call_reason: 'call_cap' }))).toBe('AAPL · call cap');
  });
});
