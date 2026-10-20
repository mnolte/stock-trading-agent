import { describe, expect, it } from 'vitest';

import type { StrategyRow } from '../types/api';
import {
  differsLine,
  disagreementFooter,
  drawdown,
  openPositionsLine,
  rowColours,
  sameAsLive,
  sameLine,
  sampleWarning,
  sessionDay,
} from './strategiesFormat';

function row(over: Partial<StrategyRow>): StrategyRow {
  return {
    id: 1,
    name: 'A',
    kind: 'llm',
    role: 'shadow',
    status: 'active',
    description: '',
    started_at: '2026-10-05T13:02:00Z',
    starting_equity: '10000',
    return_pct: null,
    raw_return_pct: null,
    vs_spy_pts: null,
    closed: 0,
    won: 0,
    lost: 0,
    avg_r: null,
    max_drawdown_pct: null,
    model_cost: null,
    same_as_live_pct: null,
    same_as_live_count: null,
    curve: [],
    ...over,
  };
}

describe('strategiesFormat', () => {
  it('shows "same as live" with its count', () => {
    expect(sameAsLive('41', 212)).toBe('41% of 212');
    expect(sameAsLive(null, null)).toBe('—');
    expect(sameAsLive('0', 0)).toBe('—');
  });

  it('writes the small-sample warning in the design wording', () => {
    const rows = [row({ closed: 3 }), row({ closed: 2 }), row({ closed: 9 })];
    expect(sampleWarning(9, rows, 20)).toBe(
      '9 sessions and 2 to 9 closed trades per strategy: far too few to tell strategies apart. ' +
        'Promoting to live needs at least 20 closed trades; read the differences as hints, not results.',
    );
    expect(sampleWarning(1, [row({ closed: 0 })], 20)).toContain('1 session and 0 closed trades per strategy');
    expect(sampleWarning(30, [row({ closed: 20 }), row({ closed: 25 })], 20)).toBeNull();
  });

  it('colours live, shadows and the baseline from the tokens', () => {
    const colours = rowColours([
      row({ role: 'live', id: null }),
      row({ role: 'shadow' }),
      row({ role: 'shadow' }),
      row({ role: 'baseline' }),
    ]);
    expect(colours.map((c) => c.hex)).toEqual(['#F2B94B', '#7FB8FF', '#C792EA', '#9FB4CC']);
  });

  it('describes differences and what stays the same', () => {
    expect(
      differsLine([{ key: 'decision.model', label: 'Model', live: 'gpt-4o-mini', shadow: 'gpt-4.1-mini' }]),
    ).toBe('model gpt-4o-mini → gpt-4.1-mini');
    const same = [
      { key: 'decision.prompt', label: 'Prompt version', live: 'decision.v1', shadow: 'decision.v1' },
      { key: 'decision.temperature', label: 'Temperature', live: '0', shadow: '0' },
      { key: 'rules.rsi_min', label: 'RSI from', live: '40', shadow: '40' },
      { key: 'rules.rsi_max', label: 'RSI to', live: '70', shadow: '70' },
      { key: 'decision.min_confidence_enter', label: 'Min confidence to enter', live: '0.55', shadow: '0.55' },
      { key: 'decision.stop_atr_min', label: 'Stop distance from', live: '1.5', shadow: '1.5' },
      { key: 'decision.stop_atr_max', label: 'Stop distance to', live: '4', shadow: '4' },
      { key: 'decision.min_reward_risk', label: 'Min reward to risk', live: '1.5', shadow: '1.5' },
    ];
    expect(sameLine(same)).toBe('prompt decision.v1, temperature 0, rules, checks, risk gates');
    expect(sameLine(same.filter((s) => s.key !== 'rules.rsi_max'))).toBe(
      'prompt decision.v1, temperature 0, checks, rsi from, risk gates',
    );
  });

  it('formats drawdown, positions, days and the footer', () => {
    expect(drawdown('0.49')).toBe('−0.49%');
    expect(drawdown('0')).toBe('0.00%');
    expect(openPositionsLine([{ symbol: 'MSFT', qty: 4 }, { symbol: 'CAT', qty: 2 }])).toBe('MSFT 4 sh · CAT 2 sh');
    expect(openPositionsLine([])).toBe('none');
    expect(sessionDay('2026-10-07')).toBe('Wed 7 Oct');
    expect(disagreementFooter(14, 9)).toBe('14 disagreements in 9 sessions · outcome after 5 sessions or at exit');
  });
});
