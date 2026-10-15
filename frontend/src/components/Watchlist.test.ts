import { describe, expect, it } from 'vitest';

import {
  actionColor,
  formatDayChange,
  formatEarnings,
  formatLastAction,
  formatPrice,
  rulesLabel,
} from './watchlistFormat';
import type { WatchlistRow } from '../types/api';

function row(status: 'pass' | 'fail' | 'held', reason: string | null): WatchlistRow {
  return {
    symbol: 'AAPL',
    price: '225.10',
    day_change_pct: '0.21',
    rules: { status, reason, checks: [] },
    earnings_date: null,
    last_action: null,
    last_action_time: null,
  };
}

describe('rulesLabel', () => {
  it('maps pass and held to design wording', () => {
    expect(rulesLabel(row('pass', null))).toBe('Passed');
    expect(rulesLabel(row('held', null))).toBe('Held · reviewed each cycle');
  });
  it('shows the failing reason for fail', () => {
    expect(rulesLabel(row('fail', 'RSI 71 > 70'))).toBe('RSI 71 > 70');
  });
});

describe('formatting', () => {
  it('formats prices with two decimals', () => {
    expect(formatPrice('225.1')).toBe('225.10');
    expect(formatPrice(null)).toBe('—');
  });

  it('formats signed day change', () => {
    expect(formatDayChange('0.74')).toBe('+0.74%');
    expect(formatDayChange('-0.21')).toBe('−0.21%');
    expect(formatDayChange(null)).toBe('—');
  });

  it('formats earnings as day + month', () => {
    expect(formatEarnings('2026-10-28')).toBe('28 Oct');
    expect(formatEarnings(null)).toBe('—');
  });

  it('formats last action with Amsterdam time', () => {
    expect(formatLastAction('skip', '2026-10-15T16:46:00+00:00')).toBe('SKIP · 18:46');
    expect(formatLastAction(null, null)).toBe('—');
    expect(formatLastAction('tighten_stop', null)).toBe('TIGHTEN STOP');
  });

  it('maps actions to colours', () => {
    expect(actionColor('enter')).toContain('positive');
    expect(actionColor('skip')).toContain('accent');
    expect(actionColor('bogus')).toContain('disabled');
  });
});
