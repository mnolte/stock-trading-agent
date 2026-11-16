import { describe, expect, it } from 'vitest';

import type { ReadinessCriterion } from '../types/api';
import { criterionSub, statusText, statusVariant } from './readinessFormat';

function crit(over: Partial<ReadinessCriterion>): ReadinessCriterion {
  return {
    key: 'avg_r',
    group: 'Evidence',
    label: 'Average R (adjusted)',
    value: '—',
    target: '≥ +0.2R',
    progress: null,
    status: 'pass',
    sample: null,
    sub: null,
    hint: '',
    estimate: null,
    estimate_date: null,
    needs: null,
    detail: {},
    ...over,
  };
}

describe('item 100 readiness wording', () => {
  it('pass stays pass', () => {
    expect(statusText(crit({ status: 'pass' }))).toBe('pass');
    expect(statusVariant(crit({ status: 'pass' }))).toBe('positive');
  });

  it('count criteria read building up', () => {
    for (const key of ['closed_trades', 'sessions']) {
      const c = crit({ key, status: 'not_yet' });
      expect(statusText(c)).toBe('building up');
      expect(statusVariant(c)).toBe('info');
    }
  });

  it('other not_yet criteria read below target', () => {
    const c = crit({ status: 'not_yet' });
    expect(statusText(c)).toBe('below target');
    expect(statusVariant(c)).toBe('negative');
  });

  it('waiting reads needs data with the sample count', () => {
    const c = crit({
      status: 'waiting',
      needs: '30 closed trades',
      sample: 3,
    });
    expect(statusText(c)).toBe('needs data');
    expect(statusVariant(c)).toBe('neutral');
    expect(criterionSub(c)).toBe('needs 30 closed trades · 3 so far');
  });

  it('a non-sample wait keeps its own sub', () => {
    const c = crit({
      status: 'waiting',
      sub: 'no active rules-only shadow',
    });
    expect(criterionSub(c)).toBe('no active rules-only shadow');
  });
});
