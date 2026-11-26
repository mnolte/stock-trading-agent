import { expect, type Page } from '@playwright/test';

import { test } from './shots';

// 075 — the Insights page reads as five questions. These specs mock the
// API (no network) with a snapshot in the shape compute_insights writes,
// including the answers block and needs/have sample states.

const ALL_DIMS = [
  ['entry_cycle', 'Entry cycle'],
  ['weekday', 'Weekday'],
  ['sector', 'Sector'],
  ['symbol', 'Symbol'],
  ['regime', 'Market regime'],
  ['days_to_earnings', 'Days to earnings'],
  ['opening_gap', 'Opening gap'],
  ['entry_vs_vwap', 'Entry vs VWAP'],
  ['pullback_depth', 'Pullback depth'],
  ['strength_vs_spy', 'Strength vs SPY'],
  ['spy_that_day', 'SPY that day'],
  ['volatility', 'Volatility'],
  ['vix_level', 'VIX level'],
  ['vix_change_5d', 'VIX 5-session change'],
  ['breadth', 'Breadth'],
  ['sector_rank', 'Sector rank'],
  ['stock_vs_sector', 'Stock vs sector'],
  ['confidence', 'Confidence'],
  ['stop_target_basis', 'Stop and target basis'],
  ['sessions_held', 'Sessions held'],
  ['prompt_model', 'Prompt and model'],
  ['release_next_morning', 'Release next morning'],
] as const;

function slicesWith(rows: Record<string, unknown[]>) {
  return Object.fromEntries(
    ALL_DIMS.map(([key, label]) => [
      key,
      { label, hidden: 0, rows: rows[key] ?? [] },
    ]),
  );
}

const ENTRY_CYCLE_ROWS = [
  { slice: '10:46', sub: '16:46 Amsterdam', trades: 31, win_rate_pct: '45.16', avg_r: '0.12', lo: '-0.18', hi: '0.42', total_r: '3.72', skips: 412, skips_missed_pct: '14.00', skips_avoided_pct: '38.00', status: 'early' },
  { slice: '11:46', sub: '17:46 Amsterdam', trades: 24, win_rate_pct: '50.00', avg_r: '0.34', lo: '-0.02', hi: '0.70', total_r: '8.16', skips: 381, skips_missed_pct: '21.00', skips_avoided_pct: '33.00', status: 'early' },
  { slice: '12:46', sub: '18:46 Amsterdam', trades: 19, win_rate_pct: '47.37', avg_r: '0.29', lo: '-0.12', hi: '0.70', total_r: '5.51', skips: 352, skips_missed_pct: '19.00', skips_avoided_pct: '31.00', status: 'early' },
  { slice: '13:46', sub: '19:46 Amsterdam', trades: 21, win_rate_pct: '52.38', avg_r: '0.41', lo: '0.02', hi: '0.80', total_r: '8.61', skips: 360, skips_missed_pct: '22.00', skips_avoided_pct: '32.00', status: 'early' },
  { slice: '14:46', sub: '20:46 Amsterdam', trades: 17, win_rate_pct: '47.06', avg_r: '0.37', lo: '-0.08', hi: '0.82', total_r: '6.29', skips: 341, skips_missed_pct: '20.00', skips_avoided_pct: '35.00', status: 'early' },
  {
    slice: '15:46', sub: '21:46 Amsterdam', trades: 7, status: 'note',
    note: 'Entries from 11 Nov, when the last-cycle rule was removed · 7 trades so far, shown from 10',
  },
];

const FULL = {
  strategies: [
    { id: 'live', name: 'Live', kind: 'llm' },
    { id: 'rules_only', name: 'Rules only', kind: 'rules_only' },
  ],
  period: 'run',
  empty: false,
  snapshot: {
    session_date: '2026-12-18',
    computed_at: '2026-12-18T21:05:00',
    trades: 112,
    answers: {
      edge: 'Probably, but not proven yet. Trades earned +0.21R on average, and the 90% range runs from +0.02R to +0.40R — the range’s low end is above zero but under 0.1R. Beta 0.31: the result doesn’t come from the market going up.',
      stands_out: '2 findings and 1 early signal. Volatility high third; Confidence 0.6–0.7; Days to earnings 4–10 days.',
      trades_lost: 'The rules stop 86% of symbol checks. After the model, the risk gates (65) and unfilled limits (24) cost the most entries. Skipping adds value: more skipped setups would have hit their stop (34%) than their target (19%).',
      conditions: 'Entry cycle: the best so far is 13:46 (+0.41R) and 14:46 (+0.37R) — 0 findings and 5 early signals.',
      costs: 'Stated confidence is off by about 20 points against what trades hit (target: within 10). 7 stops filled through the stop for 2.90R extra. Overnight holds earned −$84 while the session earned +$406.',
    },
    headline: {
      expectancy: { avg_r: '0.21', lo: '0.02', hi: '0.40', n: 112, needs: 10, have: 112 },
      edge_over_spy: { pts: '1.70', return_pct: '3.20', spy_return_pct: '4.00', exposure: '0.38', needs: 10, have: 112 },
      beta: { beta: '0.31', correlation: '0.55', sessions: 58, needs: 3, have: 58 },
      overnight: { in_session_usd: '406.00', overnight_usd: '-84.00' },
      gaps: { count: 7, extra_r: '2.90' },
      model_cost: { total_usd: '14.80', per_trade_usd: '0.13', pct_gross_profit: '2.10', needs: 10, have: 112 },
    },
    funnel: {
      steps: [
        { key: 'symbol_checks', count: 24708, losses: [] },
        { key: 'rules', count: 3410, losses: [{ label: 'rules', count: 21298 }] },
        { key: 'called', count: 2060, losses: [{ label: 'call cap', count: 1180 }, { label: 'price checks', count: 170 }] },
        { key: 'proposed', count: 214, losses: [{ label: 'model skipped', count: 1846 }] },
        { key: 'code_checks', count: 205, losses: [{ label: 'explanation check', count: 9 }] },
        { key: 'risk_gates', count: 140, losses: [{ label: 'sector cap', count: 21 }, { label: 'open risk', count: 18 }, { label: 'max positions', count: 15 }, { label: 'other', count: 11 }] },
        { key: 'filled', count: 116, losses: [{ label: 'unfilled limits', count: 24 }] },
        { key: 'closed', count: 112, losses: [{ label: 'still open', count: 4 }] },
      ],
    },
    findings: [
      {
        dimension: 'volatility', dimension_label: 'Volatility', slice: 'high third',
        status: 'finding', trades: 33, avg_r: '-0.21', rest_trades: 79, rest_avg_r: '0.33',
        sigma: '2.40', total_r: '-6.93', first_seen: '2026-12-01', held_days: 17, new: false,
      },
      {
        dimension: 'confidence', dimension_label: 'Confidence', slice: '0.6–0.7',
        status: 'finding', trades: 93, avg_r: '0.30', rest_trades: 19, rest_avg_r: '0.10',
        sigma: '2.10', total_r: '27.90', first_seen: '2026-11-20', held_days: 28, new: false,
      },
      {
        dimension: 'days_to_earnings', dimension_label: 'Days to earnings', slice: '4–10 days',
        status: 'early', trades: 18, avg_r: '-0.05', rest_trades: 94, rest_avg_r: '0.27',
        sigma: '1.80', total_r: '-0.90', first_seen: '2026-12-18', held_days: 0, new: true,
      },
    ],
    skipped: {
      total: 1846, scored: 1840, missed: 350, avoided: 626, neither: 864,
      missed_pct: '19.00', avoided_pct: '34.00', neither_pct: '47.00', baseline_r: '-0.11', not_filled: 6,
    },
    slices: slicesWith({ entry_cycle: ENTRY_CYCLE_ROWS }),
    filters_removed: [
      { filter: 'rules', count: 1412, decided: 5200, target_first: 884, stop_first: 2132, neither: 2184, r_made: '-0.31', needs: 100, have: 5200 },
      { filter: 'rr cap', count: 388, decided: 140, target_first: 30, stop_first: 70, neither: 40, r_made: '-0.08', needs: 100, have: 140 },
      { filter: 'trend gate', count: 96, decided: 22, target_first: 5, stop_first: 9, neither: 8, r_made: null, needs: 100, have: 22 },
    ],
    calibration: {
      buckets: [
        { label: '0.55–0.60', stated: '0.57', actual: '0.39', n: 41, status: 'finding' },
        { label: '0.60–0.70', stated: '0.65', actual: '0.44', n: 52, status: 'finding' },
        { label: '0.70–0.80', stated: '0.75', actual: '0.47', n: 17, status: 'early' },
        { label: '0.80+', stated: '0.85', actual: null, n: 2, status: 'hidden' },
      ],
      hidden: 2, entries: 93, weighted_gap_pts: '20.00', min_entries: 50, needs: 50, have: 93,
    },
    execution: {
      fill_rate: '82.86', filled: 116, placed: 140,
      worst_cycle: { cycle: '13:46', rate: 0.7, filled: 12, placed: 17 },
      entry_slippage_bps: '3.10', exit_slippage_bps: '6.80',
      exit_slippage_splits: [
        { reason: 'stop', bps: '11.40', n: 40 },
        { reason: 'target', bps: '0.00', n: 52 },
        { reason: 'exit', bps: '4.20', n: 20 },
      ],
      gaps_count: 7, gaps_extra_r: '2.90',
      same_day_exits: 6, same_day_avg_r: '-0.22',
      earnings_exits: 8, earnings_avg_r: '0.85', earnings_median_move_pct: '1.20',
      dividends_usd: '12.30', dividend_positions: 3,
    },
    overnight: {
      in_session_usd: '406.00', overnight_usd: '-84.00',
      nights: 214, gapped_against: 88, gapped_pct: '41.12',
      largest_gap: { r: '-1.40', symbol: 'NVDA', date: '2026-11-04' },
    },
  },
};

// The same page with under 10 closed trades: every headline number is a
// sample line, never a dash (the "strategy with under 10 trades" shot).
const SMALL = {
  ...FULL,
  snapshot: {
    ...FULL.snapshot,
    trades: 4,
    answers: {
      edge: 'Not enough closed trades yet to score the edge — the tiles below say how many are needed.',
      stands_out: 'Nothing to compare yet — an early signal needs 10 closed trades and a finding 30; this run has 4 closed trades.',
      trades_lost: 'The rules stop 90% of symbol checks.',
      conditions: 'Entry cycle: nothing to show yet — a slice starts counting at 10 closed trades.',
      costs: 'Calibration needs 50 entries with a confidence; 4 so far. No stops filled through the stop. Overnight holds earned −$12 while the session earned +$38.',
    },
    headline: {
      expectancy: { avg_r: null, lo: null, hi: null, n: 0, needs: 10, have: 4 },
      edge_over_spy: { pts: null, return_pct: null, spy_return_pct: null, exposure: null, needs: 10, have: 4 },
      beta: { beta: null, correlation: null, sessions: 1, needs: 3, have: 1 },
      overnight: { in_session_usd: '38.00', overnight_usd: '-12.00' },
      gaps: { count: 0, extra_r: null },
      model_cost: { total_usd: '0.80', per_trade_usd: null, pct_gross_profit: null, needs: 10, have: 4 },
    },
    funnel: {
      steps: [
        { key: 'symbol_checks', count: 852, losses: [] },
        { key: 'rules', count: 96, losses: [{ label: 'rules', count: 756 }] },
        { key: 'called', count: 60, losses: [] },
        { key: 'proposed', count: 9, losses: [{ label: 'model skipped', count: 51 }] },
        { key: 'code_checks', count: 9, losses: [] },
        { key: 'risk_gates', count: 6, losses: [{ label: 'sector cap', count: 3 }] },
        { key: 'filled', count: 5, losses: [{ label: 'unfilled limits', count: 1 }] },
        { key: 'closed', count: 4, losses: [{ label: 'still open', count: 1 }] },
      ],
    },
    findings: [],
    skipped: {
      total: 51, scored: 48, missed: 9, avoided: 17, neither: 22,
      missed_pct: '19.00', avoided_pct: '35.00', neither_pct: '46.00', baseline_r: '-0.20', not_filled: 3,
    },
    slices: slicesWith({}),
    filters_removed: [
      { filter: 'rules', count: 96, decided: 12, target_first: 3, stop_first: 4, neither: 5, r_made: null, needs: 100, have: 12 },
    ],
    calibration: {
      buckets: [
        { label: '0.5–0.6', stated: '0.55', actual: null, n: 4, status: 'hidden' },
      ],
      hidden: 4, entries: 4, weighted_gap_pts: null, min_entries: 50, needs: 50, have: 4,
    },
    execution: {
      fill_rate: '80.00', filled: 4, placed: 5,
      worst_cycle: null,
      entry_slippage_bps: '2.40', exit_slippage_bps: '3.10',
      exit_slippage_splits: [],
      gaps_count: 0, gaps_extra_r: null,
      same_day_exits: 1, same_day_avg_r: '-0.40',
      earnings_exits: 0, earnings_avg_r: null, earnings_median_move_pct: null,
      dividends_usd: '0.00', dividend_positions: 0,
    },
    overnight: {
      in_session_usd: '38.00', overnight_usd: '-12.00',
      nights: 6, gapped_against: 2, gapped_pct: '33.33',
      largest_gap: null,
    },
  },
};

async function mockInsights(page: Page) {
  await page.route('**/api/insights?**', (route) => {
    const strategy = new URL(route.request().url()).searchParams.get('strategy');
    return route.fulfill({
      contentType: 'application/json',
      json: strategy === 'rules_only' ? SMALL : FULL,
    });
  });
}

function shot(project: string, name: string): string {
  const dir = process.env.SCREENSHOT_DIR || '/tmp/shots';
  return `${dir}/${name}-${project === 'mobile' ? '390' : '1440'}.png`;
}

async function noItemNumbers(page: Page) {
  const text = await page.locator('main').innerText();
  expect(text).not.toMatch(/item \d{3}/);
}

test('insights reads as five questions', async ({ page }, testInfo) => {
  await mockInsights(page);
  await page.goto('/insights');
  await expect(
    page.locator('main').getByRole('heading', { name: 'Insights' }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('heading', { name: 'Is there an edge?' })).toBeVisible();
  await expect(page.getByText('Probably, but not proven yet')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What stands out?' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Findings and early signals' })).toBeVisible();
  await expect(page.getByText('Entries from 11 Nov, when the last-cycle rule was removed')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Where are trades lost?' })).toBeVisible();
  await expect(page.getByText('86% of symbol checks')).toBeVisible();
  await expect(page.getByText('14% kept').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Which conditions work?' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What do confidence, fills and nights cost?' })).toBeVisible();
  // the grouped dimension select, not 21 chips
  const select = page.getByLabel('Dimension');
  await expect(select).toBeVisible();
  await expect(select.locator('optgroup[label="Timing"]')).toHaveCount(1);
  await expect(select.locator('optgroup[label="Market"]')).toHaveCount(1);
  await expect(select.locator('optgroup[label="Stock"]')).toHaveCount(1);
  await expect(select.locator('optgroup[label="Model"]')).toHaveCount(1);
  // ?dim= persists the choice
  await select.selectOption('breadth');
  await expect(page).toHaveURL(/dim=breadth/);
  await select.selectOption('entry_cycle');
  // no sideways scroll and no item numbers in the rendered text
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await noItemNumbers(page);
  await page.screenshot({ path: shot(testInfo.project.name, 'Insights'), fullPage: true });
});

test('dimension select open', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop select shot');
  await mockInsights(page);
  await page.goto('/insights');
  const select = page.getByLabel('Dimension');
  await expect(select).toBeVisible({ timeout: 60_000 });
  await select.click();
  await page.screenshot({ path: shot(testInfo.project.name, 'Insights-select'), fullPage: false });
});

test('under 10 trades shows sample lines, not dashes', async ({ page }, testInfo) => {
  await mockInsights(page);
  await page.goto('/insights');
  await page.getByRole('button', { name: 'Rules only' }).click();
  await expect(page.getByText('needs 10 closed trades · 4 so far').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('needs 3 sessions · 1 so far')).toBeVisible();
  await expect(page.getByText('needs 50 entries · 4 so far').first()).toBeVisible();
  await expect(page.getByText('needs 100 · 12 so far')).toBeVisible();
  await noItemNumbers(page);
  await page.screenshot({ path: shot(testInfo.project.name, 'Insights-few-trades'), fullPage: true });
});
