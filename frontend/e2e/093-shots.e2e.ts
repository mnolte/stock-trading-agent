import { expect, type Page } from '@playwright/test';

import { test, screenshotDir } from './shots';

// 093 — the Universe pool row that kept its place with a failed history
// check: the line under the chip shows the screen's numbers and the
// current ones, in both states (history loaded / still missing). The
// API is mocked in the shape GET /api/universe and
// GET /api/universe/sync return (docs/SPEC.md, Universe screen →
// Status per symbol, item 093).

const HISTORY_DETAIL = '258 of 259 daily bars · 59 of 59 sessions';

function poolRows() {
  return [
    {
      symbol: 'AAPL',
      name: 'Apple',
      sector: 'Technology',
      status: 'eligible_fails',
      rank: 3,
      fails: 1,
      failed_check: 'history',
      detail: HISTORY_DETAIL,
      history_now: { loaded: true, detail: '260 of 260 daily bars · 60 of 60 sessions' },
      screen_date: '2026-11-13',
      last_close: '252.10',
      avg_dollar_volume: '12400000000',
      return_vs_spy: '2.2',
    },
    {
      symbol: 'MSFT',
      name: 'Microsoft',
      sector: 'Technology',
      status: 'eligible_fails',
      rank: 4,
      fails: 1,
      failed_check: 'history',
      detail: HISTORY_DETAIL,
      history_now: { loaded: false, detail: '259 of 260 daily bars · 60 of 60 sessions' },
      screen_date: '2026-11-13',
      last_close: '421.30',
      avg_dollar_volume: '9800000000',
      return_vs_spy: '3.9',
    },
    {
      symbol: 'NVDA',
      name: 'Nvidia',
      sector: 'Technology',
      status: 'eligible',
      rank: 1,
      fails: 0,
      failed_check: null,
      detail: '',
      history_now: null,
      screen_date: '2026-11-13',
      last_close: '188.40',
      avg_dollar_volume: '31200000000',
      return_vs_spy: '6.8',
    },
    {
      symbol: 'BKNG',
      name: 'Booking',
      sector: 'Consumer Discretionary',
      status: 'excluded',
      rank: null,
      fails: 3,
      failed_check: 'one_share',
      detail: '1 share $5,184 > max position $2,036',
      history_now: null,
      screen_date: '2026-11-13',
      last_close: '5184.00',
      avg_dollar_volume: '1150000000',
      return_vs_spy: '1.2',
    },
  ].map((r) => ({
    added_at: '2026-09-01T12:00:00',
    progress: null,
    next_screen: null,
    held: false,
    qty: 0,
    screened: true,
    in_pool: true,
    ...r,
  }));
}

const UNIVERSE = {
  mode: 'screened',
  preview: false,
  allowed_sectors: ['Technology', 'Consumer Discretionary'],
  fixed_symbols: [],
  held_symbols: [],
  screen_time_ny: '08:10',
  history: { daily_sessions: 260, sessions_5m: 60 },
  thresholds: {
    min_price: 10,
    min_avg_dollar_volume: 100000000,
    lookback_sessions: 20,
    drop_after_failed_screens: 3,
    max_position_pct: 20,
    max_calls_per_cycle: 10,
  },
  pool: { size: 4, max: 150 },
  counts: { pool: 4, eligible: 2, ready: 0, excluded: 1, warming_up: 0, held: 0 },
  screen: {
    session_date: '2026-11-13',
    failed: false,
    equity: '10000.00',
    max_position_usd: '2036.00',
    funnel: [
      { key: 'pool', count: 4, out: [] },
      { key: 'tradable', count: 4, out: [] },
      { key: 'history', count: 3, out: ['MSFT'] },
      { key: 'min_price', count: 3, out: [] },
      { key: 'one_share', count: 2, out: ['BKNG'] },
      { key: 'dollar_volume', count: 2, out: [] },
    ],
    eligible_total: 2,
  },
  sectors: [
    { sector: 'Technology', pool: 3, eligible: 1, rank: 1, rank_total: 2 },
    { sector: 'Consumer Discretionary', pool: 1, eligible: 0, rank: null, rank_total: 2 },
  ],
  rows: poolRows(),
};

const SYNC = {
  jobs: [
    {
      job: 'screen', label: 'Daily screen', status: 'ok',
      started_at: '2026-11-13T12:10:00Z', finished_at: '2026-11-13T12:10:09Z',
      duration_seconds: 9, done: 72, total: 72, failures: [],
      schedule: '08:10 NY · trading days', late: false,
    },
  ],
  history: null,
  max_requests: 20,
};

async function mockUniverse(page: Page) {
  await page.route('**/api/universe**', (route) => {
    const url = route.request().url();
    if (url.includes('/api/universe/sync')) {
      return route.fulfill({ contentType: 'application/json', json: SYNC });
    }
    return route.fulfill({ contentType: 'application/json', json: UNIVERSE });
  });
}

function shot(testInfo: { project: { name: string } }, name: string): string {
  return `${screenshotDir('093')}/${name}-${testInfo.project.name === 'mobile' ? '390' : '1440'}.png`;
}

test('universe 093: live history detail on a history-fail row', async ({ page }, testInfo) => {
  await mockUniverse(page);
  await page.goto('/universe');
  await expect(page.getByText('screened pool')).toBeVisible({ timeout: 60_000 });
  // Both sentences: loaded and still missing.
  await expect(
    page.getByText(/Now: history loaded\. The fail clears at the next passing screen\./),
  ).toBeVisible();
  await expect(
    page.getByText(/Now: 259 of 260 daily bars · 60 of 60 sessions\./),
  ).toBeVisible();
  // The chip stays the screen's record.
  await expect(
    page.locator('td', { hasText: 'eligible · 1 fail' }).first(),
  ).toBeVisible();
  const width = page.viewportSize()?.width ?? 1440;
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(width + 1);
  await page.screenshot({ path: shot(testInfo, 'universe-093-history-now'), fullPage: true });
});
