import { describe, expect, it } from 'vitest';

import type { BacktestEstimate } from '../types/api';
import { estimateVerdicts } from './backtestEstimate';

function est(over: Partial<BacktestEstimate> = {}): BacktestEstimate {
  return {
    sessions: 690,
    bars_to_download: 312000,
    download_calls: 1600,
    download_minutes: 40,
    calls: 20700,
    cache_hits: 3900,
    cost_usd: 6.7,
    cap_cost_usd: 8.1,
    cap_remaining_usd: 8.54,
    may_stop_early: false,
    disabled_reason: null,
    cutoff: null,
    cutoff_label: null,
    strategies: [
      {
        ref: 'live',
        name: 'Live settings',
        kind: 'llm',
        model: 'gpt-4.1-mini',
        calls: 20700,
        cache_hits: 3900,
        cost_usd: 6.7,
        cutoff: '2023-10-01',
      },
    ],
    ...over,
  };
}

describe('estimateVerdicts', () => {
  it('says the run fits the budget and starts after the cutoff', () => {
    const v = estimateVerdicts(est(), '2024-01-02');
    expect(v.budget).toEqual({
      ok: true,
      text: 'Fits this month’s budget: about $6.70 of the $8.54 left.',
    });
    expect(v.cutoff).toEqual({
      ok: true,
      text: 'Starts after gpt-4.1-mini’s knowledge cutoff (Oct 2023).',
    });
    expect(v.stopEarly).toBeNull();
  });

  it('says when the estimate does not fit the budget', () => {
    const v = estimateVerdicts(est({ cost_usd: 12.4 }), '2024-01-02');
    expect(v.budget).toEqual({
      ok: false,
      text: 'Doesn’t fit this month’s budget: about $12.40 against the $8.54 left.',
    });
  });

  it('warns when the cap price may stop the run early', () => {
    const v = estimateVerdicts(est({ may_stop_early: true }), '2024-01-02');
    expect(v.stopEarly?.ok).toBe(false);
    expect(v.stopEarly?.text).toContain('May stop early');
    expect(v.stopEarly?.text).toContain('$8.10');
  });

  it('refuses when the period starts before a model cutoff', () => {
    const v = estimateVerdicts(
      est({
        cutoff: '2023-10-01',
        cutoff_label:
          'gpt-4.1-mini’s knowledge cutoff is 2023-10-01 — the earliest start is 2023-10-02',
        disabled_reason:
          'gpt-4.1-mini’s knowledge cutoff is 2023-10-01 — the earliest start is 2023-10-02',
      }),
      '2023-06-01',
    );
    expect(v.cutoff).toEqual({
      ok: false,
      text: 'gpt-4.1-mini’s knowledge cutoff is 2023-10-01 — the earliest start is 2023-10-02',
    });
  });

  it('has no cutoff verdict for rules-only runs', () => {
    const v = estimateVerdicts(
      est({ strategies: [{ ref: 'rules_only', name: 'Rules only', kind: 'rules_only', model: null, calls: 0, cache_hits: 0, cost_usd: 0, cutoff: null }] }),
      '2024-01-02',
    );
    expect(v.cutoff).toBeNull();
  });
});
