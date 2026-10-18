import { describe, expect, it } from 'vitest';

import type { ReviewListItem, WeeklyFacts } from '../types/api';
import {
  decidedLabel,
  dueDayLabel,
  isoWeek,
  metaLine,
  numVariant,
  signedPct,
  signedPts,
  signedR,
  upcomingWeek,
  usd,
  verdictLabel,
  verdictVariant,
  weekLabel,
  writtenLabel,
} from './reviewsFormat';

const facts = (over: Partial<WeeklyFacts>): WeeklyFacts =>
  ({
    period: { week: 41, start: '2026-10-05', end: '2026-10-09' },
    return_pct: 1.49,
    ...over,
  }) as WeeklyFacts;

describe('signed figures', () => {
  it('formats signed percents and points', () => {
    expect(signedPct(1.49)).toBe('+1.49%');
    expect(signedPct(-0.49)).toBe('−0.49%');
    expect(signedPct(0)).toBe('0.00%');
    expect(signedPct(null)).toBe('—');
    expect(signedPts(0.76)).toBe('+0.76 pts');
    expect(signedPts(-1.2)).toBe('−1.20 pts');
  });

  it('formats signed R', () => {
    expect(signedR(1.07)).toBe('+1.07R');
    expect(signedR(-1)).toBe('−1.00R');
    expect(signedR(null)).toBe('—');
  });

  it('formats usd with separators', () => {
    expect(usd(10148.8)).toBe('$10,148.80');
    expect(usd(0.002)).toBe('$0.00');
    expect(usd(null)).toBe('—');
  });

  it('maps sign to a variant', () => {
    expect(numVariant(1)).toBe('positive');
    expect(numVariant(-1)).toBe('negative');
    expect(numVariant(0)).toBe('neutral');
    expect(numVariant(null)).toBe('neutral');
  });
});

describe('dates and labels', () => {
  it('formats the week label', () => {
    expect(weekLabel(facts({}))).toBe('Week 41 · 5 – 9 Oct');
  });

  it('formats the decided date in Amsterdam time', () => {
    // 14:46 UTC = 16:46 CEST — same Wednesday.
    expect(decidedLabel('2026-10-07T14:46:00+00:00')).toBe('Wed 7 Oct');
  });

  it('formats the due day and the written timestamp', () => {
    expect(dueDayLabel('2026-10-22T20:00:00+00:00')).toBe('22 Oct');
    // 08:00 UTC = 10:00 CEST.
    expect(writtenLabel('2026-10-17T08:00:00+00:00')).toBe('Sat 17 Oct 10:00');
  });

  it('computes ISO weeks', () => {
    expect(isoWeek('2026-10-05')).toBe(41);
    expect(isoWeek('2026-01-01')).toBe(1);
  });

  it('labels the upcoming week from its due Saturday', () => {
    // Due Sat 17 Oct covers Mon 12 – Fri 16 Oct.
    expect(upcomingWeek('2026-10-17T08:00:00+00:00')).toEqual({
      label: 'Week 42',
      sub: '12 – 16 Oct',
      due: 'due Sat 17 Oct',
      period: '2026-W42',
    });
  });
});

describe('metaLine', () => {
  const item = (over: Partial<ReviewListItem>): ReviewListItem => ({
    id: 1,
    kind: 'weekly',
    period: '2026-W41',
    label: 'Week 41',
    sub: '5 – 9 Oct',
    source: 'model',
    reason: null,
    due_at: '2026-10-10T08:00:00+00:00',
    created_at: null,
    ...over,
  });

  it('names the model path', () => {
    expect(
      metaLine(item({ model: 'gpt-4o-mini', prompt_version: 'weekly.v1' })),
    ).toBe(
      'Written Sat 10 Oct 10:00 · gpt-4o-mini · weekly.v1 · numbers checked ✓',
    );
  });

  it('names the fallback reason for template reviews', () => {
    expect(
      metaLine(item({ source: 'template', reason: 'number check failed' })),
    ).toBe('Written Sat 10 Oct 10:00 · Plain review: number check failed');
  });
});

describe('verdicts', () => {
  it('labels and colours the three verdict values', () => {
    expect(verdictLabel('good_call')).toBe('Good call');
    expect(verdictLabel('bad_call')).toBe('Bad call');
    expect(verdictLabel('unclear')).toBe('Unclear');
    expect(verdictVariant('good_call')).toBe('positive');
    expect(verdictVariant('bad_call')).toBe('negative');
    expect(verdictVariant('unclear')).toBe('neutral');
  });
});
