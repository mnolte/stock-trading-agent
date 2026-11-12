import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 076 screenshots are for docs/screenshots/076/ (pass SCREENSHOT_DIR).
// performance-1440/-390 show the page under 20 closed trades; the
// -pending pair shows "After the exit" folded into its one line.
const outDir = screenshotDir('076');

const shot = (project: string, name: string) =>
  path.join(
    outDir,
    `performance-${name ? `${name}-` : ''}${project === 'mobile' ? '390' : '1440'}.png`,
  );

const PENDING_TAILS = {
  empty: false,
  run: { id: 1, started_at: '2026-10-05T13:30:00Z', current: true },
  trades: 6,
  min_trades: 20,
  after_exit_sessions: 5,
  top_share_pct: 20,
  pending: 6,
  unavailable: 0,
  excluded: 0,
  first_result_date: '2026-10-22',
  distribution: [],
  largest_win: null,
  largest_loss: null,
  top_share: null,
  net_without_best: null,
  winners_kept: null,
  after_exit: [],
  rows: [
    { id: 1, symbol: 'QQQ', exit_reason: 'target', exit_label: 'target', result_r: '1.6', best_open_r: '1.6', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-06T13:30:00Z', closed_at: '2026-10-14T20:00:00Z' },
    { id: 2, symbol: 'GOOGL', exit_reason: 'exit', exit_label: 'agent exit', result_r: '0.9', best_open_r: '1.2', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-06T14:00:00Z', closed_at: '2026-10-14T20:00:00Z' },
    { id: 3, symbol: 'NVDA', exit_reason: 'stop', exit_label: 'stop', result_r: '-1.0', best_open_r: '0.4', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-07T13:30:00Z', closed_at: '2026-10-14T19:00:00Z' },
    { id: 4, symbol: 'META', exit_reason: 'target', exit_label: 'target', result_r: '2.6', best_open_r: '2.6', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-08T13:30:00Z', closed_at: '2026-10-14T20:00:00Z' },
    { id: 5, symbol: 'AAPL', exit_reason: 'target', exit_label: 'target', result_r: '1.6', best_open_r: '1.6', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-08T14:30:00Z', closed_at: '2026-10-14T20:00:00Z' },
    { id: 6, symbol: 'XOM', exit_reason: 'stop', exit_label: 'stop', result_r: '-1.1', best_open_r: '0.2', after_best_r: null, after_close_r: null, after_exit_status: 'pending', opened_at: '2026-10-07T13:30:00Z', closed_at: '2026-10-14T20:00:00Z' },
  ],
};

test('performance: summary, tiles, curve, folded rows', async ({ page }, testInfo) => {
  await page.goto('/performance');
  await expect(
    page.getByRole('heading', { name: 'Performance', level: 1 }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel('Summary')).toBeVisible();
  await expect(page.getByText('Equity vs SPY buy-and-hold')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Winners and tails' }),
  ).toBeVisible();
  await page.screenshot({ path: shot(testInfo.project.name, ''), fullPage: true });
});

test('performance: after the exit all pending', async ({ page }, testInfo) => {
  await page.route('**/api/performance/tails**', (route) =>
    route.fulfill({ contentType: 'application/json', json: PENDING_TAILS }),
  );
  await page.goto('/performance');
  await expect(
    page.getByText(/trades are inside their 5-session window/),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: shot(testInfo.project.name, 'pending'),
    fullPage: true,
  });
});
