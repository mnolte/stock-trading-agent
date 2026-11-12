import { expect } from '@playwright/test';

import { test } from './shots';

function shot(project: string, name: string): string {
  const dir = process.env.SCREENSHOT_DIR || '/tmp/shots';
  return `${dir}/${name}-${project === 'mobile' ? '390' : '1440'}.png`;
}

const SECTORS_NOW = {
  at: '2026-10-15T19:46:00',
  breadth: {
    above_sma50_pct: 31,
    above_sma200_pct: 44,
    ema_up_pct: 36,
    up_today_pct: 44,
  },
  sectors: [
    { sector: 'Real Estate', etf: 'XLRE', rank: 1, rs_vs_spy_pct: 2.97 },
    { sector: 'Technology', etf: 'XLK', rank: 2, rs_vs_spy_pct: 2.31 },
    { sector: 'Energy', etf: 'XLE', rank: 3, rs_vs_spy_pct: 1.84 },
    { sector: 'Consumer Discretionary', etf: 'XLY', rank: 4, rs_vs_spy_pct: 1.12 },
    { sector: 'Financials', etf: 'XLF', rank: 5, rs_vs_spy_pct: 0.84 },
    { sector: 'Health Care', etf: 'XLV', rank: 6, rs_vs_spy_pct: 0.61 },
    { sector: 'Industrials', etf: 'XLI', rank: 7, rs_vs_spy_pct: 0.15 },
    { sector: 'Communication Services', etf: 'XLC', rank: 8, rs_vs_spy_pct: -0.42 },
    { sector: 'Consumer Staples', etf: 'XLP', rank: 9, rs_vs_spy_pct: -0.18 },
    { sector: 'Materials', etf: 'XLB', rank: 10, rs_vs_spy_pct: -1.05 },
    { sector: 'Utilities', etf: 'XLU', rank: 11, rs_vs_spy_pct: -1.62 },
  ],
};

const SNAP = {
  strategies: [
    { id: 'live', name: 'Live', kind: 'llm' },
    { id: '3', name: 'B · Bigger model', kind: 'llm' },
  ],
  period: 'run',
  empty: false,
  snapshot: {
    session_date: '2026-12-18',
    computed_at: '2026-12-18T21:05:00',
    trades: 112,
    headline: {
      expectancy: { avg_r: '0.31', lo: '0.09', hi: '0.53', n: 112 },
      edge_over_spy: { pts: '1.84', return_pct: '3.10', spy_return_pct: '5.04', exposure: '0.25' },
      beta: { beta: '0.42', correlation: '0.61', sessions: 20 },
      overnight: { in_session_usd: '1840.20', overnight_usd: '-312.40' },
      gaps: { count: 2, extra_r: '0.46' },
      model_cost: { total_usd: '6.72', per_trade_usd: '0.06', pct_gross_profit: '1.40' },
    },
    funnel: {
      steps: [
        { key: 'symbol_checks', count: 1740, losses: [] },
        { key: 'rules', count: 1620, losses: [{ label: 'rules', count: 120 }] },
        { key: 'called', count: 1580, losses: [{ label: 'call cap', count: 30 }, { label: 'price checks', count: 10 }] },
        { key: 'proposed', count: 148, losses: [{ label: 'model skipped', count: 1400 }, { label: 'invalid output', count: 20 }, { label: 'call failed', count: 12 }] },
        { key: 'code_checks', count: 140, losses: [{ label: 'rr', count: 8 }] },
        { key: 'risk_gates', count: 132, losses: [{ label: 'sector cap', count: 8 }] },
        { key: 'filled', count: 120, losses: [{ label: 'unfilled limits', count: 12 }] },
        { key: 'closed', count: 112, losses: [{ label: 'still open', count: 8 }] },
      ],
    },
    findings: [
      {
        dimension: 'entry_cycle', dimension_label: 'Entry cycle', slice: '10:46',
        status: 'finding', trades: 40, avg_r: '0.62', rest_trades: 72, rest_avg_r: '0.14',
        sigma: '3.40', total_r: '24.80', first_seen: '2026-12-01', held_days: 17, new: false,
      },
    ],
    skipped: {
      total: 1400, scored: 1396, missed: 210, avoided: 392, neither: 794,
      missed_pct: '15.00', avoided_pct: '28.00', neither_pct: '57.00', baseline_r: '-0.18', not_filled: 62,
    },
    slices: {
      entry_cycle: {
        label: 'Entry cycle',
        hidden: 0,
        rows: [
          { slice: '10:46', sub: '16:46 Amsterdam', trades: 40, win_rate_pct: '65.00', avg_r: '0.62', lo: '0.30', hi: '0.94', total_r: '24.80', skips: 480, skips_missed_pct: '12.00', status: 'finding' },
          { slice: '11:46', trades: 30, win_rate_pct: '53.33', avg_r: '0.20', lo: '-0.10', hi: '0.50', total_r: '6.00', skips: 320, skips_missed_pct: '16.00', status: 'counted' },
        ],
      },
      weekday: { label: 'Weekday', hidden: 0, rows: [] },
      sector: { label: 'Sector', hidden: 0, rows: [] },
      symbol: { label: 'Symbol', hidden: 0, rows: [] },
      regime: { label: 'Market regime', hidden: 0, rows: [] },
      days_to_earnings: { label: 'Days to earnings', hidden: 0, rows: [] },
      opening_gap: { label: 'Opening gap', hidden: 0, rows: [] },
      entry_vs_vwap: { label: 'Entry vs VWAP', hidden: 0, rows: [] },
      pullback_depth: { label: 'Pullback depth', hidden: 0, rows: [] },
      strength_vs_spy: { label: 'Strength vs SPY', hidden: 0, rows: [] },
      spy_that_day: { label: 'SPY that day', hidden: 0, rows: [] },
      volatility: { label: 'Volatility', hidden: 0, rows: [] },
      breadth: {
        label: 'Breadth',
        hidden: 0,
        rows: [
          { slice: 'low third', trades: 18, win_rate_pct: '38.89', avg_r: '-0.22', lo: '-0.55', hi: '0.11', total_r: '-3.96', skips: 120, skips_missed_pct: '21.00', status: 'counted' },
          { slice: 'middle third', trades: 70, win_rate_pct: '55.71', avg_r: '0.30', lo: '0.05', hi: '0.55', total_r: '21.00', skips: 430, skips_missed_pct: '14.00', status: 'counted' },
          { slice: 'high third', trades: 24, win_rate_pct: '70.83', avg_r: '0.71', lo: '0.32', hi: '1.10', total_r: '17.04', skips: 90, skips_missed_pct: '9.00', status: 'finding' },
        ],
      },
      sector_rank: {
        label: 'Sector rank',
        hidden: 0,
        rows: [
          { slice: '1–3', trades: 30, win_rate_pct: '73.33', avg_r: '0.78', lo: '0.41', hi: '1.15', total_r: '23.40', skips: 140, skips_missed_pct: '8.00', status: 'finding' },
          { slice: '4–8', trades: 34, win_rate_pct: '55.88', avg_r: '0.28', lo: '-0.05', hi: '0.61', total_r: '9.52', skips: 160, skips_missed_pct: '15.00', status: 'counted' },
          { slice: '9–11', trades: 22, win_rate_pct: '36.36', avg_r: '-0.35', lo: '-0.72', hi: '0.02', total_r: '-7.70', skips: 130, skips_missed_pct: '24.00', status: 'finding' },
        ],
      },
      stock_vs_sector: {
        label: 'Stock vs sector',
        hidden: 0,
        rows: [
          { slice: 'above 0', trades: 62, win_rate_pct: '62.90', avg_r: '0.48', lo: '0.15', hi: '0.81', total_r: '29.76', skips: 330, skips_missed_pct: '12.00', status: 'finding' },
          { slice: 'below 0', trades: 50, win_rate_pct: '44.00', avg_r: '-0.09', lo: '-0.40', hi: '0.22', total_r: '-4.50', skips: 290, skips_missed_pct: '19.00', status: 'counted' },
        ],
      },
      confidence: { label: 'Confidence', hidden: 0, rows: [] },
      stop_target_basis: { label: 'Stop and target basis', hidden: 0, rows: [] },
      sessions_held: { label: 'Sessions held', hidden: 0, rows: [] },
      prompt_model: { label: 'Prompt and model', hidden: 0, rows: [] },
    },
    calibration: {
      buckets: [
        { label: '0.5–0.6', stated: '0.55', actual: '0.50', n: 36, status: 'finding' },
        { label: '0.6–0.7', stated: '0.64', actual: '0.61', n: 48, status: 'finding' },
        { label: '0.7–0.8', stated: '0.74', actual: '0.70', n: 17, status: 'early' },
        { label: '0.8–0.9', stated: '0.83', actual: '0.78', n: 9, status: 'hidden' },
      ],
      hidden: 11, entries: 110, weighted_gap_pts: '4.20',
    },
    execution: {
      fill_rate: '92.31', filled: 120, placed: 130,
      worst_cycle: { cycle: '14:46', rate: 0.8, filled: 8, placed: 10 },
      entry_slippage_bps: '1.20', exit_slippage_bps: '3.40',
      exit_slippage_splits: [
        { reason: 'stop', bps: '8.10', n: 40 },
        { reason: 'target', bps: '0.00', n: 52 },
        { reason: 'exit', bps: '4.20', n: 20 },
      ],
      gaps_count: 2, gaps_extra_r: '0.46',
      same_day_exits: 6, same_day_avg_r: '-0.30',
      earnings_exits: 8, earnings_avg_r: '0.85', earnings_median_move_pct: '1.20',
      dividends_usd: '12.30', dividend_positions: 3,
    },
    overnight: {
      in_session_usd: '1840.20', overnight_usd: '-312.40',
      nights: 96, gapped_against: 41, gapped_pct: '42.71',
      largest_gap: { r: '-0.82', symbol: 'NVDA', date: '2026-12-09' },
    },
  },
  sectors_now: SECTORS_NOW,
};

async function mockInsights(page: import('@playwright/test').Page) {
  await page.route('**/api/insights?**', (route) =>
    route.fulfill({ contentType: 'application/json', json: SNAP }),
  );
}

test('watchlist with the Sector column', async ({ page }, testInfo) => {
  // Item 073: rank/sector/earnings hide below 760 px — the phone rows are
  // two lines (symbol/price/day, then rules + last action).
  test.skip(testInfo.project.name === 'mobile', 'sector column is desktop-only');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Watchlist' })).toBeVisible({
    timeout: 60_000,
  });
  // The column header row is aria-hidden (decorative grid labels), so
  // match it by text; the data cells carry the real assertion. Rows
  // land after the /api/watchlist fetch — wait on them first.
  const watchlist = page.locator('div[role="list"][aria-label="Watchlist"]').first();
  await expect(watchlist.getByText('XLK 2/11').first()).toBeVisible();
  await expect(watchlist.getByText('XLE 3/11').first()).toBeVisible();
  await expect(watchlist.getByText('Sector', { exact: true })).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name, 'watchlist'), fullPage: false });
});

test('sectors now card', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop card shot');
  await mockInsights(page);
  await page.goto('/insights');
  const card = page.locator('section').filter({ hasText: 'Sectors now' }).first();
  await expect(card).toBeVisible({ timeout: 60_000 });
  await expect(card.getByText('vs SPY · 20 × 1h bars')).toBeVisible();
  await expect(card.getByText('XLRE')).toBeVisible();
  await expect(card.getByText(/Breadth then: 31% of the universe above SMA 50, 44% up today/)).toBeVisible();
  await card.screenshot({ path: shot(testInfo.project.name, 'sectors-now') });
});

test('insights breadth slice', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop slice shots');
  await mockInsights(page);
  await page.goto('/insights');
  // 075: the 21 dimension chips are one grouped <select> now.
  const select = page.getByLabel('Dimension');
  await expect(select).toBeVisible({ timeout: 60_000 });
  await select.selectOption('breadth');
  await expect(page.getByText('high third').first()).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name, 'insights-breadth'), fullPage: true });
});

test('insights sector rank slice', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop slice shots');
  await mockInsights(page);
  await page.goto('/insights');
  const select = page.getByLabel('Dimension');
  await expect(select).toBeVisible({ timeout: 60_000 });
  await select.selectOption('sector_rank');
  await expect(page.getByText('1–3').first()).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name, 'insights-sector-rank'), fullPage: true });
});

test('insights stock vs sector slice', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop slice shots');
  await mockInsights(page);
  await page.goto('/insights');
  const select = page.getByLabel('Dimension');
  await expect(select).toBeVisible({ timeout: 60_000 });
  await select.selectOption('stock_vs_sector');
  // "above 0" also appears in the (closed) how-to-read fold — scope to the cell.
  await expect(page.getByRole('cell', { name: 'above 0' })).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name, 'insights-stock-vs-sector'), fullPage: true });
});
