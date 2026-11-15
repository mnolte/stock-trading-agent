import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 100 — the UX pass on Strategies and Backtest (PR 1 of 3): small-sample
// banner, closed-trades bars, aria-disabled New shadow/Promote with their
// reasons, the disagreements tally + filters + "who was better", the
// Backtest page order (Runs, Results, form on a phone), the "Before you
// run" verdicts and the stored answer sentence. The page APIs are mocked
// in the shape /api/strategies and /api/backtests return.

const outDir = screenshotDir('100');
const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

function curve(days: number, from: number, drift: number) {
  const out = [];
  for (let i = 0; i <= days; i++) {
    const d = new Date(Date.UTC(2026, 8, 21 + i));
    out.push({
      date: d.toISOString().slice(0, 10),
      pct: (from + drift * i + Math.sin(i / 2) * 0.4).toFixed(2),
    });
  }
  return out;
}

const STRATEGIES = {
  synthetic_quote_entries: 1,
  run: { id: 4, started_at: '2026-09-21T13:30:00Z', sessions: 9 },
  rows: [
    {
      id: null,
      name: 'Live',
      kind: 'llm',
      role: 'live',
      status: 'active',
      description: 'decision.v1 · gpt-4.1-mini · settings v3',
      started_at: '2026-09-21T13:30:00Z',
      starting_equity: '10000',
      return_pct: '1.84',
      raw_return_pct: '1.91',
      vs_spy_pts: '0.62',
      closed: 9,
      won: 5,
      lost: 4,
      avg_r: '0.18',
      max_drawdown_pct: '-1.10',
      model_cost: '1.44',
      same_as_live_pct: null,
      same_as_live_count: null,
      curve: curve(9, 0, 0.2),
    },
    {
      id: 11,
      name: 'B · Bigger model',
      short: 'B',
      kind: 'llm',
      role: 'shadow',
      status: 'active',
      description: 'decision.v1 · gpt-4o-mini · temp 0.0',
      started_at: '2026-09-21T13:30:00Z',
      starting_equity: '10000',
      return_pct: '2.61',
      raw_return_pct: '2.61',
      vs_spy_pts: '1.39',
      closed: 9,
      won: 6,
      lost: 3,
      avg_r: '0.31',
      max_drawdown_pct: '-0.80',
      model_cost: '0.92',
      same_as_live_pct: '77.78',
      same_as_live_count: 18,
      curve: curve(9, 0, 0.3),
      prompt_version: 'decision.v1',
      model: 'gpt-4o-mini',
      temperature: 0,
      overrides: {},
      calls: 41,
      missed: 0,
      entries: 6,
      synthetic_entries: 1,
      open_positions: [{ symbol: 'MSFT', qty: 4 }],
      differences: {
        differs: [{ key: 'decision.model', label: 'Model', live: 'gpt-4.1-mini', shadow: 'gpt-4o-mini' }],
        same: [{ key: 'risk', label: 'Risk', live: '1.0%', shadow: '1.0%' }],
      },
      promote: {
        allowed: false,
        closed: 9,
        needed: 20,
        reason: 'Promote needs 20 closed trades · B has 9.',
      },
      disagreements: {
        total: 14,
        sessions: 9,
        counts: { shadow_better: 3, live_better: 2, undecided: 9 },
        items: [
          {
            time: '2026-10-13T14:46:00Z', date: '2026-10-13', symbol: 'NVDA',
            live_action: 'skip', live_label: 'SKIP 0.55', shadow_action: 'enter', shadow_label: 'ENTER 0.72',
            live_decision_id: 901, shadow_decision_id: 1901,
            result: 'B -1.0R stop', result_text: 'B’s trade: −1.0R at the stop',
            better: 'live', tone: 'negative',
          },
          {
            time: '2026-10-12T15:46:00Z', date: '2026-10-12', symbol: 'MSFT',
            live_action: 'skip', live_label: 'SKIP 0.60', shadow_action: 'enter', shadow_label: 'ENTER 0.80',
            live_decision_id: 902, shadow_decision_id: 1902,
            result: 'B +2.1R target', result_text: 'B’s trade: +2.1R at the target',
            better: 'shadow', tone: 'positive',
          },
          {
            time: '2026-10-10T13:46:00Z', date: '2026-10-10', symbol: 'AAPL',
            live_action: 'enter', live_label: 'ENTER 0.68', shadow_action: 'skip', shadow_label: 'SKIP 0.42',
            live_decision_id: 903, shadow_decision_id: 1903,
            result: 'live open', result_text: 'Live’s trade is open',
            better: null, tone: 'neutral',
          },
          {
            time: '2026-10-09T14:16:00Z', date: '2026-10-09', symbol: 'AMZN',
            live_action: 'skip', live_label: 'SKIP 0.50', shadow_action: 'enter', shadow_label: 'ENTER 0.66',
            live_decision_id: 904, shadow_decision_id: 1904,
            result: '+1.2% after 5 sessions', result_text: '+1.2% after 5 sessions',
            better: null, tone: 'positive',
          },
        ],
      },
    },
    {
      id: 12,
      name: 'C · Rules only',
      short: 'C',
      kind: 'rules_only',
      role: 'baseline',
      status: 'active',
      description: 'no model · rules only',
      started_at: '2026-09-21T13:30:00Z',
      starting_equity: '10000',
      return_pct: '0.44',
      raw_return_pct: '0.44',
      vs_spy_pts: '-0.78',
      closed: 4,
      won: 2,
      lost: 2,
      avg_r: '0.05',
      max_drawdown_pct: '-0.40',
      model_cost: '0',
      same_as_live_pct: '50.00',
      same_as_live_count: 8,
      curve: curve(9, 0, 0.05),
      calls: 0,
      missed: 0,
      entries: 4,
      synthetic_entries: 0,
      open_positions: [],
      differences: { differs: [], same: [] },
      promote: { allowed: false, closed: 4, needed: 20, reason: 'Promote needs 20 closed trades · C has 4.' },
      disagreements: { total: 0, sessions: 0, counts: { shadow_better: 0, live_better: 0, undecided: 0 }, items: [] },
    },
  ],
  spy_curve: curve(9, 0, 0.15),
  cost: { month_usd: '2.36', cap_usd: '5.00', cap_reached: false },
  slots: { used: 3, max: 3 },
  promote_min_closed_trades: 20,
  rules_only: { stop_atr: 1.5, target_r: 2 },
  options: {
    cycles: ['10:46', '11:46'],
    models: ['gpt-4o-mini'],
    prompts: ['decision.v1'],
    live: { prompt_version: 'decision.v1', model: 'gpt-4.1-mini', temperature: 0.2 },
    overrides: [],
  },
};

function btCurve(days: number, equity: number, drift: number) {
  const out = [];
  for (let i = 0; i <= days; i++) {
    const d = new Date(Date.UTC(2026, 0, 2 + i));
    out.push({ date: d.toISOString().slice(0, 10), equity: equity + drift * i + Math.sin(i / 3) * 60 });
  }
  return out;
}

const BT_RUN = {
  id: 4,
  name: 'Since 2024 · live vs rules',
  start: '2026-01-02',
  end: '2026-09-30',
  universe_mode: 'fixed',
  strategies: [
    { ref: 'live', name: 'Live settings v3', kind: 'llm', slug: 'live', model: 'gpt-4.1-mini' },
    { ref: 'rules_only', name: 'Rules only', kind: 'rules_only', slug: 'rules-only' },
  ],
  starting_equity: '10000',
  slippage_pct: '0.05',
  status: 'done',
  progress: '10 of 10 sessions',
  cost_usd: 6.7,
  process_id: null,
  size_bytes: 812_000_000,
  warnings: [
    'Earnings dates missing for 4 symbol-days: entries were blocked those days (treated as inside the blackout).',
    'Stop and target touched inside the same 5-min bar 9 times: counted as the stop (the worse case).',
  ],
  summary: {
    answer:
      'Live settings v3 made +18.6% where buying and holding SPY made +39.9%: 21.3 points less, with a deepest fall of 5.2% against 18.9% and money in the market 24% of the time. Rules only made +4.1%, 14.5 points less than Live settings v3.',
    live: {
      slug: 'live', name: 'Live settings v3', kind: 'llm',
      return_pct: 18.6, vs_spy_pts: -21.3, spy_return_pct: 39.9, max_drawdown_pct: -5.2,
      closed_trades: 214, win_rate: 0.48, avg_r: 0.24, profit_factor: 1.36, time_in_market: 0.24,
      decisions: 3900, calls: 20700, cache_hits: 3900, cost_usd: 6.1,
      curve: btCurve(40, 10000, 47), spy_curve: btCurve(40, 10000, 100),
      by_symbol: {}, by_regime: {
        up_normal_vol: { trades: 121, avg_r: 0.38 },
        up_high_vol: { trades: 44, avg_r: 0.09 },
        sideways: { trades: 41, avg_r: 0.02 },
        down_normal_vol: { trades: 8, avg_r: -0.3 },
      },
    },
    'rules-only': {
      slug: 'rules-only', name: 'Rules only', kind: 'rules_only',
      return_pct: 4.1, vs_spy_pts: -35.8, spy_return_pct: 39.9, max_drawdown_pct: -9.7,
      closed_trades: 588, win_rate: 0.39, avg_r: 0.04, profit_factor: 1.06, time_in_market: 0.41,
      decisions: 3900, calls: 0, cache_hits: 0, cost_usd: 0,
      curve: btCurve(40, 10000, 10), spy_curve: btCurve(40, 10000, 100),
      by_symbol: {}, by_regime: {
        up_normal_vol: { trades: 262, avg_r: 0.12 },
        up_high_vol: { trades: 131, avg_r: -0.05 },
        sideways: { trades: 149, avg_r: -0.03 },
        down_normal_vol: { trades: 46, avg_r: -0.11 },
      },
    },
  },
  refuse_reason: null,
  created_at: '2026-10-14T20:00:00Z',
  finished_at: '2026-10-14T22:31:00Z',
};

const BACKTESTS = {
  runs: [
    BT_RUN,
    { ...BT_RUN, id: 3, name: 'Jun 2025', status: 'cancelled', summary: null, progress: null, warnings: null, cost_usd: 1.94 },
    {
      ...BT_RUN, id: 2, name: 'Refused', status: 'refused', summary: null, progress: null,
      warnings: null, cost_usd: 0,
      refuse_reason: 'Period starts before gpt-4.1-mini’s knowledge cutoff',
    },
  ],
  store: { size_bytes: 812_000_000, earliest: '2024-01-02', latest: '2026-10-13', month_spent_usd: 11.46 },
  monthly_cap: 20,
  available: {
    live: { ref: 'live', name: 'Live settings', model: 'gpt-4.1-mini', prompt_version: 'decision.v1' },
    rules_only: { ref: 'rules_only', name: 'Rules only' },
    shadows: [
      { ref: 'strategy:11', name: 'B · Bigger model', kind: 'llm', model: 'gpt-4o-mini', prompt_version: 'decision.v1' },
      { ref: 'strategy:12', name: 'C · Rules only', kind: 'rules_only', model: null, prompt_version: null },
    ],
    priced_models: ['gpt-4.1-mini', 'gpt-4o-mini'],
    model_options: [
      { id: 'gpt-4.1-mini', provider: 'openai' },
      { id: 'gpt-4o-mini', provider: 'openai' },
    ],
    model_cutoffs: { 'gpt-4.1-mini': '2023-10-01', 'gpt-4o-mini': '2023-10-01' },
    universe: { mode: 'fixed', fixed_count: 8 },
  },
};

const ESTIMATE = {
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
    { ref: 'live', name: 'Live settings', kind: 'llm', model: 'gpt-4.1-mini', calls: 20700, cache_hits: 3900, cost_usd: 6.7, cutoff: '2023-10-01' },
    { ref: 'rules_only', name: 'Rules only', kind: 'rules_only', model: null, calls: 0, cache_hits: 0, cost_usd: 0, cutoff: null },
  ],
};

test('strategies: banner, bars, reasons and disagreements', async ({ page }, testInfo) => {
  await page.route('**/api/strategies', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ contentType: 'application/json', json: STRATEGIES });
    }
    return route.fulfill({ status: 200, json: {} });
  });
  await page.goto('/strategies');
  await expect(page.getByRole('heading', { name: 'Compare', exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Too early to tell these strategies apart.')).toBeVisible();
  await expect(page.getByText('All 3 slots in use · archive a shadow to add one')).toBeVisible();
  await expect(page.getByText('Promote needs 20 closed trades · B has 9.')).toBeVisible();
  await expect(page.getByText(/disagreements in 9 sessions/)).toBeVisible();
  // The disagreement list needs a click into B's row? No — B is selected
  // by default (first shadow). Check the tally and a better chip.
  await expect(page.getByText('B’s trade: −1.0R at the stop')).toBeVisible();
  await page.screenshot({ path: shot('strategies', testInfo.project.name), fullPage: true });
});

test('backtest: runs first, verdicts, answer sentence', async ({ page }, testInfo) => {
  await page.route('**/api/backtests', (route) =>
    route.fulfill({ contentType: 'application/json', json: BACKTESTS }),
  );
  await page.route('**/api/backtests/estimate', (route) =>
    route.fulfill({ contentType: 'application/json', json: ESTIMATE }),
  );
  await page.goto('/backtest');
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Live settings v3 made +18.6%')).toBeVisible();
  await expect(page.getByText('This is evidence about the past, never a forecast.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'View 802 trades' }).or(page.getByRole('button', { name: /View \d+ trades/ }))).toBeVisible();
  // Fill the estimate: pick a period so the POST fires.
  await page.getByRole('button', { name: 'Since 2024', exact: true }).click();
  await expect(page.getByText('Before you run')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Fits this month’s budget/)).toBeVisible();
  await page.screenshot({ path: shot('backtest', testInfo.project.name), fullPage: true });
});
