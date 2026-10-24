import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 19 screenshots are for docs/screenshots/pr-19/ (pass SCREENSHOT_DIR),
// next to the design references Performance.png and Performance-390.png.
const outDir = screenshotDir('pr-19');
const shot = (project: string) =>
  path.join(outDir, `performance-${project === 'mobile' ? '390' : '1440'}.png`);

// The tails numbers mirror the 6 closed trades in design/screens/Performance.dc.html.
const TAILS = {
  empty: false,
  run: { id: 1, started_at: '2026-10-05T13:30:00Z', current: true },
  trades: 6,
  min_trades: 20,
  after_exit_sessions: 5,
  top_share_pct: 20,
  pending: 0,
  unavailable: 0,
  excluded: 0,
  distribution: [
    { lower: null, upper: '-1.5', count: 0 },
    { lower: '-1.5', upper: '-1.0', count: 2 },
    { lower: '-1.0', upper: '-0.5', count: 0 },
    { lower: '-0.5', upper: '0.0', count: 0 },
    { lower: '0.0', upper: '0.5', count: 0 },
    { lower: '0.5', upper: '1.0', count: 1 },
    { lower: '1.0', upper: '1.5', count: 0 },
    { lower: '1.5', upper: '2.0', count: 2 },
    { lower: '2.0', upper: '2.5', count: 0 },
    { lower: '2.5', upper: '3.0', count: 1 },
    { lower: '3.0', upper: null, count: 0 },
  ],
  largest_win: {
    symbol: 'META', exit_reason: 'target', r: '2.6',
    opened_at: '2026-10-08T13:30:00Z', closed_at: '2026-10-09T20:00:00Z',
    below_stop: false,
  },
  largest_loss: {
    symbol: 'XOM', exit_reason: 'stop', r: '-1.05',
    opened_at: '2026-10-07T13:30:00Z', closed_at: '2026-10-08T20:00:00Z',
    below_stop: true,
  },
  top_share: { trades: 2, of: 6, share: '0.66' },
  net_without_best: { net: '63.27', total: '147.63', symbol: 'META' },
  winners_kept: { share: '0.94', winners: 4 },
  after_exit: [
    { reason: 'target', label: 'target', count: 3, median_best_r: '1.1', median_close_r: '0.4', share_1r: '0.6667', recovered_above_entry: 3 },
    { reason: 'stop', label: 'stop', count: 2, median_best_r: '0.85', median_close_r: '0.15', share_1r: null, recovered_above_entry: 1 },
    { reason: 'exit', label: 'agent exit', count: 1, median_best_r: '0.5', median_close_r: '-0.2', share_1r: null, recovered_above_entry: 1 },
  ],
  rows: [
    { id: 1, symbol: 'QQQ', exit_reason: 'target', exit_label: 'target', result_r: '1.6', best_open_r: '1.6', after_best_r: '1.3', after_close_r: '0.4', after_exit_status: 'done', opened_at: '2026-10-06T13:30:00Z', closed_at: '2026-10-08T20:00:00Z' },
    { id: 2, symbol: 'GOOGL', exit_reason: 'exit', exit_label: 'agent exit', result_r: '0.9', best_open_r: '1.2', after_best_r: '0.5', after_close_r: '-0.2', after_exit_status: 'done', opened_at: '2026-10-06T14:00:00Z', closed_at: '2026-10-09T20:00:00Z' },
    { id: 3, symbol: 'NVDA', exit_reason: 'stop', exit_label: 'stop', result_r: '-1.0', best_open_r: '0.4', after_best_r: '1.4', after_close_r: '0.9', after_exit_status: 'done', opened_at: '2026-10-07T13:30:00Z', closed_at: '2026-10-08T19:00:00Z' },
    { id: 4, symbol: 'META', exit_reason: 'target', exit_label: 'target', result_r: '2.6', best_open_r: '2.6', after_best_r: '0.6', after_close_r: '-0.3', after_exit_status: 'done', opened_at: '2026-10-08T13:30:00Z', closed_at: '2026-10-09T20:00:00Z' },
    { id: 5, symbol: 'AAPL', exit_reason: 'target', exit_label: 'target', result_r: '1.6', best_open_r: '1.6', after_best_r: '1.1', after_close_r: '0.8', after_exit_status: 'done', opened_at: '2026-10-08T14:30:00Z', closed_at: '2026-10-12T20:00:00Z' },
    { id: 6, symbol: 'XOM', exit_reason: 'stop', exit_label: 'stop', result_r: '-1.1', best_open_r: '0.2', after_best_r: '0.3', after_close_r: '-0.6', after_exit_status: 'done', opened_at: '2026-10-07T13:30:00Z', closed_at: '2026-10-08T20:00:00Z' },
  ],
};

test('performance: winners and tails card', async ({ page }, testInfo) => {
  await page.route('**/api/performance/tails**', (route) =>
    route.fulfill({ contentType: 'application/json', json: TAILS }),
  );
  await page.goto('/performance');
  await expect(page.getByRole('heading', { name: 'Winners and tails' })).toBeVisible({ timeout: 60_000 });

  // Small-sample banner below min_trades.
  await expect(page.getByText('6 of 20 trades')).toBeVisible();
  await expect(page.getByText(/Too few closed trades to read the tail yet/)).toBeVisible();

  // R distribution with its aria-label.
  await expect(
    page.getByRole('img', { name: /Distribution of closed trades in R/ }),
  ).toBeVisible();

  // Tiles with hover definitions.
  await expect(page.getByText('Largest win')).toBeVisible();
  await expect(page.getByText('Largest loss')).toBeVisible();
  await expect(page.getByText('Top 20% of trades')).toBeVisible();
  await expect(page.getByText('Net without the best trade')).toBeVisible();
  await expect(page.getByText('Winners kept')).toBeVisible();

  // After the exit.
  await expect(page.getByRole('heading', { name: 'After the exit' })).toBeVisible();
  await expect(page.getByText('Target exits · best after')).toBeVisible();
  await expect(page.getByText('Stop exits · 5-session close')).toBeVisible();
  await expect(page.getByText('2 of 3 ran ≥ 1R further')).toBeVisible();
  await expect(page.getByText('How to read it:')).toBeVisible();

  await page.screenshot({ path: shot(testInfo.project.name), fullPage: true });
});
