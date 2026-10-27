import { expect } from '@playwright/test';

import { test } from './shots';

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
      {
        dimension: 'opening_gap', dimension_label: 'Opening gap', slice: 'down >1%',
        status: 'early', trades: 14, avg_r: '-0.55', rest_trades: 98, rest_avg_r: '0.43',
        sigma: '2.90', total_r: '-7.70', first_seen: '2026-12-18', held_days: 0, new: true,
      },
    ],
    skipped: {
      total: 1400, scored: 1396, missed: 210, avoided: 392, neither: 794,
      missed_pct: '15.00', avoided_pct: '28.00', neither_pct: '57.00', baseline_r: '-0.18',
    },
    slices: {
      entry_cycle: {
        label: 'Entry cycle',
        hidden: 4,
        rows: [
          { slice: '10:46', trades: 40, win_rate_pct: '65.00', avg_r: '0.62', lo: '0.30', hi: '0.94', total_r: '24.80', skips: 480, skips_missed_pct: '12.00', status: 'finding' },
          { slice: '11:46', trades: 30, win_rate_pct: '53.33', avg_r: '0.20', lo: '-0.10', hi: '0.50', total_r: '6.00', skips: 320, skips_missed_pct: '16.00', status: 'counted' },
          { slice: '12:46', trades: 16, win_rate_pct: '50.00', avg_r: '0.05', lo: '-0.40', hi: '0.50', total_r: '0.80', skips: 240, skips_missed_pct: '14.00', status: 'early' },
          { slice: '13:46', trades: 12, win_rate_pct: '41.67', avg_r: '-0.10', lo: '-0.60', hi: '0.40', total_r: '-1.20', skips: 180, skips_missed_pct: '18.00', status: 'early' },
          { slice: '14:46', trades: 10, win_rate_pct: '40.00', avg_r: '0.02', lo: '-0.70', hi: '0.74', total_r: '0.20', skips: 120, skips_missed_pct: '13.00', status: 'early' },
          { slice: '15:46', trades: 0, status: 'rule', rule: 'No entries in the last cycle of a session (rule). Held positions are still reviewed.' },
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
};

function shot(project: string): string {
  const dir = process.env.SCREENSHOT_DIR || '/tmp/shots';
  return `${dir}/${project === 'mobile' ? 'Insights-390' : 'Insights'}.png`;
}

test('insights: headline, findings, funnel and slices', async ({ page }, testInfo) => {
  await page.route('**/api/insights**', (route) =>
    route.fulfill({ contentType: 'application/json', json: SNAP }),
  );
  await page.goto('/insights');
  await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Expectancy')).toBeVisible();
  await expect(page.getByText('+0.31R')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible();
  await expect(page.getByText('Entry cycle: 10:46')).toBeVisible();
  await expect(page.getByText('Test in a shadow')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Where trades come from' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Skipped setups' })).toBeVisible();
  await expect(page.getByText('No entries in the last cycle')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Calibration' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Execution' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Overnight' })).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name), fullPage: true });
});
