import { describe, expect, it } from 'vitest';

import type { VixPayload } from './types/api';
import {
  fmtVixClose,
  fmtVixPct,
  ordinal,
  vixCardTitle,
  vixChipLabel,
  vixHeaderTitle,
  vixPhoneTitle,
} from './vix';

const vix: VixPayload = {
  close: '17.44',
  date: '2026-10-13',
  percentile: '42.4',
  change_5d_pct: '-6.2',
  vixy_today_pct: '0.8',
  vixy_since_vix_pct: '1.2',
  stale: false,
};

describe('vix formatting', () => {
  it('formats the close at one decimal and missing as —', () => {
    expect(fmtVixClose('17.44')).toBe('17.4');
    expect(fmtVixClose(null)).toBe('—');
  });

  it('ordinals', () => {
    expect(ordinal(1)).toBe('1st');
    expect(ordinal(2)).toBe('2nd');
    expect(ordinal(3)).toBe('3rd');
    expect(ordinal(4)).toBe('4th');
    expect(ordinal(11)).toBe('11th');
    expect(ordinal(13)).toBe('13th');
    expect(ordinal(42.4)).toBe('42nd');
  });

  it('signed percents with one decimal, .0 trimmed', () => {
    expect(fmtVixPct('1.2')).toBe('+1.2%');
    expect(fmtVixPct('-6.0')).toBe('−6%');
    expect(fmtVixPct(null)).toBeNull();
  });
});

describe('vixChipLabel', () => {
  it('close plus ordinal percentile', () => {
    expect(vixChipLabel(vix)).toBe('VIX 17.4 · 42nd pct');
  });
  it('close only when percentile is missing', () => {
    expect(vixChipLabel({ ...vix, percentile: null })).toBe('VIX 17.4');
  });
  it('null when there is no close', () => {
    expect(vixChipLabel({ ...vix, close: null })).toBeNull();
  });
});

describe('vixHeaderTitle', () => {
  it('carries every field for a fresh close', () => {
    const title = vixHeaderTitle(vix);
    expect(title).toContain('expected volatility over the next 30 days');
    expect(title).toContain('42nd percentile of the last year');
    expect(title).toContain('5 sessions −6.2%');
    expect(title).toContain('VIXY since that close +1.2%, today +0.8%');
  });
  it('stale explains the lag instead', () => {
    const title = vixHeaderTitle({ ...vix, stale: true });
    expect(title).toContain('vix_stale');
    expect(title).not.toContain('percentile');
  });
});

describe('vixPhoneTitle / vixCardTitle', () => {
  it('phone regime chip carries the VIX summary', () => {
    const title = vixPhoneTitle(vix);
    expect(title).toContain('Regime from SPY');
    expect(title).toContain('VIX 17.4');
    expect(title).toContain('42nd percentile');
  });
  it('phone title is just the regime without VIX', () => {
    expect(vixPhoneTitle(null)).toBe('Regime from SPY');
    expect(vixPhoneTitle({ ...vix, close: null })).toBe('Regime from SPY');
  });
  it('card chip names the cycle', () => {
    const title = vixCardTitle(vix);
    expect(title).toContain('VIX at this cycle');
    expect(title).toContain('5 sessions −6.2%');
  });
});
