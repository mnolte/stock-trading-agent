// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ReviewDetail, WeeklyFacts } from '../types/api';
import { WeeklyView } from './Reviews';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const facts = (over: Partial<WeeklyFacts>): WeeklyFacts =>
  ({
    period: { week: 41, start: '2026-10-05', end: '2026-10-09' },
    limits: { verdict_after_sessions: 5, skip_flat_pct: 2 },
    return_pct: 3.4,
    return_usd: 340,
    equity_start: 10000,
    equity_end: 10340,
    spy_return_pct: 2.6,
    vs_spy_pts: 0.8,
    max_drawdown_pct: -1.9,
    trades_closed: 12,
    won: 7,
    lost: 5,
    avg_r: 0.72,
    model_cost_usd: 0.42,
    model_calls: 96,
    reviews_cost_usd: 0.0015,
    trades: [],
    best_trade: null,
    worst_trade: null,
    skips: {
      total: 0,
      with_outcome: 0,
      pending: 0,
      up: 0,
      flat: 0,
      down: 0,
      biggest_miss: null,
    },
    verdicts: {
      good_call: 0,
      bad_call: 0,
      unclear: 0,
      total: 0,
      entered: { good_call: 0, bad_call: 0, unclear: 0 },
      blocked: { good_call: 0, bad_call: 0, unclear: 0, reasons: {} },
      skips: { good_call: 0, bad_call: 0, unclear: 0 },
      thesis_contradictions: 0,
    },
    suggestion_allowed: false,
    by_regime: [],
    by_symbol: [],
    ...over,
  }) as WeeklyFacts;

const review = (f: WeeklyFacts): ReviewDetail => ({
  id: 1,
  kind: 'weekly',
  period: '2026-W41',
  label: 'Week 41',
  sub: '',
  source: 'model',
  reason: null,
  due_at: null,
  created_at: null,
  model: 'gpt-4o-mini',
  prompt_version: 'weekly.v1',
  facts: f as unknown as Record<string, unknown>,
  text: 'Week 41 summary.',
  content: null,
  status_line: null,
  tokens: null,
  cost: null,
});

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

async function render(f: WeeklyFacts) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <WeeklyView review={review(f)} />
      </MemoryRouter>,
    );
  });
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('WeeklyView facts.trades', () => {
  it('accepts the legacy count shape without crashing (056)', async () => {
    await render(facts({ trades: 12 as unknown as WeeklyFacts['trades'] }));
    expect(container.textContent).toContain(
      '12 trades closed — the per-trade list isn’t stored for this review.',
    );
  });

  it('renders the per-trade list when trades is a list', async () => {
    await render(
      facts({
        trades: [
          {
            symbol: 'MSFT',
            opened: '2026-09-15',
            closed: '2026-09-21',
            exit: 'target',
            r: 1.9,
            pnl_usd: 412.5,
            regime: 'Up · normal vol',
          },
        ],
      }),
    );
    expect(container.textContent).toContain('MSFT');
    expect(container.textContent).toContain('target');
  });
});
