import { describe, expect, it } from 'vitest';

import type { LlmCallRow } from '../types/api';
import {
  apiFilters,
  chipText,
  DEFAULT_SOURCES,
  groupBySession,
  moreFilterCount,
  parseFilters,
  resolveRange,
  reviewTitle,
  rowContext,
  rowTitle,
  toSearchParams,
  type InspectorFilters,
} from './inspectorFilters';

const DATES = ['2026-10-15', '2026-10-14', '2026-10-13', '2026-10-12', '2026-10-09'];

function callRow(partial: Partial<LlmCallRow>): LlmCallRow {
  return {
    id: 1,
    sent_at: '2026-10-15T16:46:00Z',
    session_date: '2026-10-15',
    source: 'live',
    kind: 'decision',
    strategy_id: null,
    strategy: null,
    decision_id: 1,
    shadow_decision_id: null,
    review_id: null,
    verdict_id: null,
    cycle_id: 7,
    symbol: 'AAPL',
    context: 'candidate',
    model: 'gpt-4o-mini',
    temperature: 0,
    prompt_version: 'decision.v1',
    prompt_hash: 'abc',
    tokens_in: 1800,
    tokens_out: 130,
    cost: 0.00035,
    cost_source: 'api',
    latency_ms: 1500,
    final_action: 'skip',
    outcome: 'success',
    reasons: [],
    error_type: null,
    http_status: null,
    backfilled: false,
    ...partial,
  };
}

describe('parseFilters', () => {
  it('defaults to Last session, all outcomes, live + reviews', () => {
    const f = parseFilters(new URLSearchParams());
    expect(f.period).toBe('last');
    expect(f.outcome).toBeUndefined();
    expect(f.sources).toEqual(DEFAULT_SOURCES);
    expect(f.order).toBe('desc');
    expect(f.call).toBeUndefined();
  });

  it('reads every filter from the URL', () => {
    const f = parseFilters(
      new URLSearchParams(
        'period=custom&from=2026-10-10&to=2026-10-15&outcome=warning&source=live,shadow&symbol=aapl&action=skip&reason=slow&model=gpt-4o-mini&prompt=decision.v1&strategy=momentum&cycle=42&order=asc&call=7',
      ),
    );
    expect(f).toMatchObject({
      period: 'custom',
      customFrom: '2026-10-10',
      customTo: '2026-10-15',
      outcome: 'warning',
      sources: ['live', 'shadow'],
      symbol: 'aapl',
      action: 'skip',
      reason: 'slow',
      model: 'gpt-4o-mini',
      prompt: 'decision.v1',
      strategy: 'momentum',
      cycle: '42',
      order: 'asc',
      call: 7,
    });
  });

  it('treats an explicit from/to without period as Custom', () => {
    const f = parseFilters(new URLSearchParams('from=2026-10-10&to=2026-10-15'));
    expect(f.period).toBe('custom');
  });

  it('keeps unknown period values from breaking the default', () => {
    const f = parseFilters(new URLSearchParams('period=bogus'));
    expect(f.period).toBe('last');
  });
});

describe('resolveRange', () => {
  const base: InspectorFilters = {
    period: 'last',
    sources: [...DEFAULT_SOURCES],
    order: 'desc',
  };

  it('Last session is the newest session date', () => {
    expect(resolveRange(base, DATES)).toEqual({
      from: '2026-10-15',
      to: '2026-10-15',
    });
  });

  it('5 sessions reaches back four days; 20 clamps at the list', () => {
    expect(resolveRange({ ...base, period: '5' }, DATES)).toEqual({
      from: '2026-10-09',
      to: '2026-10-15',
    });
    expect(resolveRange({ ...base, period: '20' }, DATES).to).toBe('2026-10-15');
    expect(resolveRange({ ...base, period: '20' }, DATES).from).toBe('2026-10-09');
  });

  it('All sends no range; Custom uses the inputs verbatim', () => {
    expect(resolveRange({ ...base, period: 'all' }, DATES)).toEqual({});
    expect(
      resolveRange(
        {
          ...base,
          period: 'custom',
          customFrom: '2026-10-10',
          customTo: '2026-10-12',
        },
        DATES,
      ),
    ).toEqual({ from: '2026-10-10', to: '2026-10-12' });
  });

  it('an empty calendar resolves to no range', () => {
    expect(resolveRange(base, [])).toEqual({});
  });
});

describe('apiFilters and the URL round trip', () => {
  it('joins sources and carries the resolved range', () => {
    const f = apiFilters(
      {
        period: 'last',
        sources: ['live'],
        symbol: 'AAPL',
        order: 'desc',
      },
      DATES,
    );
    expect(f).toMatchObject({
      from: '2026-10-15',
      to: '2026-10-15',
      source: 'live',
      symbol: 'AAPL',
    });
    expect(f.outcome).toBeUndefined();
  });

  it('parse(toSearchParams(f)) is f', () => {
    const original: InspectorFilters = {
      period: 'custom',
      customFrom: '2026-10-10',
      customTo: '2026-10-15',
      outcome: 'error',
      sources: ['shadow'],
      symbol: 'msft',
      reason: 'timeout',
      order: 'asc',
      call: 42,
    };
    expect(parseFilters(toSearchParams(original))).toEqual(original);
  });

  it('drops defaults from the URL so links stay short', () => {
    const params = toSearchParams({
      period: 'last',
      sources: [...DEFAULT_SOURCES],
      order: 'desc',
    });
    expect(params.get('source')).toBeNull();
    expect(params.get('order')).toBeNull();
    expect(params.get('period')).toBe('last');
  });
});

describe('groupBySession', () => {
  it('merges consecutive rows under their date, keeping order', () => {
    const groups = groupBySession([
      callRow({ id: 3 }),
      callRow({ id: 2 }),
      callRow({ id: 1, session_date: '2026-10-14' }),
    ]);
    expect(groups.map((g) => g.date)).toEqual(['2026-10-15', '2026-10-14']);
    expect(groups[0].calls.map((c) => c.id)).toEqual([3, 2]);
  });
});

describe('row labels', () => {
  it('shows the symbol for decisions, the kind for reviews', () => {
    expect(rowTitle(callRow({}))).toBe('AAPL');
    expect(rowTitle(callRow({ source: 'review', kind: 'verdict' }))).toBe('Verdict');
    expect(reviewTitle('briefing')).toBe('Daily briefing');
    expect(reviewTitle('verdict')).toBe('Verdict draft');
  });

  it('composes the context line per source', () => {
    expect(rowContext(callRow({ context: 'held' }))).toBe('held');
    expect(
      rowContext(
        callRow({ source: 'shadow', strategy: 'momentum v2', context: 'candidate' }),
      ),
    ).toBe('shadow · momentum v2 · candidate');
    expect(
      rowContext(callRow({ source: 'review', kind: 'briefing', prompt_version: 'briefing.v1' })),
    ).toBe('review · briefing.v1');
  });

  it('chip text is success or the reason labels', () => {
    expect(chipText(callRow({}))).toBe('success');
    expect(
      chipText(callRow({ outcome: 'warning', reasons: ['changed_by_code', 'slow'] })),
    ).toBe('changed by code, slow');
    expect(
      chipText(callRow({ outcome: 'error', reasons: ['api_error'] })),
    ).toBe('API error');
  });

  it('counts only the more-filters dims', () => {
    expect(
      moreFilterCount({
        period: 'last',
        sources: [...DEFAULT_SOURCES],
        order: 'desc',
        symbol: 'AAPL',
        prompt: 'decision.v1',
        outcome: 'error', // a top-level filter — not counted
      }),
    ).toBe(2);
  });
});
