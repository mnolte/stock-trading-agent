import { expect, type Page } from '@playwright/test';

import { test, screenshotDir } from './shots';

// 077 — the Universe page: one mode line, six tiles, Data sync (open with
// a failed job and a running history load), Today's screen with the
// fold, Pool by sector and the pool table with Status third. The API is
// mocked with a snapshot in the shape GET /api/universe and
// GET /api/universe/sync return (docs/SPEC.md, Universe screen).

const SYMBOLS = [
  ['NVDA', 'Nvidia', 'Technology'],
  ['AVGO', 'Broadcom', 'Technology'],
  ['GE', 'GE Aerospace', 'Industrials'],
  ['MSFT', 'Microsoft', 'Technology'],
  ['JPM', 'JPMorgan Chase', 'Financials'],
  ['AAPL', 'Apple', 'Technology'],
  ['CAT', 'Caterpillar', 'Industrials'],
  ['PFE', 'Pfizer', 'Health Care'],
  ['DDOG', 'Datadog', 'Technology'],
  ['ANET', 'Arista Networks', 'Technology'],
  ['BKNG', 'Booking', 'Consumer Discretionary'],
  ['AZO', 'AutoZone', 'Consumer Discretionary'],
] as const;

function poolRows() {
  const bySymbol: Record<string, object> = {
    NVDA: { status: 'eligible', rank: 1, last_close: '188.40', avg_dollar_volume: '31200000000', return_vs_spy: '6.8' },
    AVGO: { status: 'eligible', rank: 2, last_close: '342.10', avg_dollar_volume: '6900000000', return_vs_spy: '5.1' },
    GE: { status: 'eligible_fails', rank: 3, fails: 1, last_close: '298.60', avg_dollar_volume: '1450000000', return_vs_spy: '4.4' },
    MSFT: { status: 'eligible', rank: 4, last_close: '421.30', avg_dollar_volume: '9800000000', return_vs_spy: '3.9', held: true, qty: 4 },
    JPM: { status: 'eligible', rank: 5, last_close: '233.10', avg_dollar_volume: '2600000000', return_vs_spy: '2.7', held: true, qty: 8 },
    AAPL: { status: 'eligible', rank: 6, last_close: '252.10', avg_dollar_volume: '12400000000', return_vs_spy: '2.2' },
    CAT: { status: 'eligible', rank: 7, last_close: '512.80', avg_dollar_volume: '1280000000', return_vs_spy: '2.0' },
    PFE: { status: 'eligible', rank: 69, last_close: '24.80', avg_dollar_volume: '1060000000', return_vs_spy: '-4.9' },
    DDOG: {
      status: 'ready', rank: null, last_close: '131.70', avg_dollar_volume: '1240000000', return_vs_spy: '1.9',
      next_screen: '08:10 NY Thu 15 Oct',
      detail: 'history loaded · joins at the next screen · 08:10 NY Thu 15 Oct',
    },
    ANET: {
      status: 'warming_up', rank: null, last_close: '142.30', avg_dollar_volume: '1900000000', return_vs_spy: null,
      progress: { daily: { have: 180, need: 260 }, m5: { have: 22, need: 60 } },
      detail: '180 of 260 daily bars · 22 of 60 sessions',
    },
    BKNG: {
      status: 'excluded', rank: null, last_close: '5184.00', avg_dollar_volume: '1150000000', return_vs_spy: '1.2',
      failed_check: 'one_share', detail: '1 share $5,184 > max position $2,036',
    },
    AZO: {
      status: 'excluded', rank: null, last_close: '3890.50', avg_dollar_volume: '420000000', return_vs_spy: '-0.8',
      failed_check: 'one_share', detail: '1 share $3,890.50 > max position $2,036',
    },
  };
  return SYMBOLS.map(([symbol, name, sector]) => ({
    symbol,
    name: symbol === 'PFE' ? null : name,
    sector,
    added_at: '2026-09-01T12:00:00',
    progress: null,
    next_screen: null,
    fails: 0,
    failed_check: null,
    detail: '',
    held: false,
    qty: 0,
    screened: true,
    in_pool: true,
    ...bySymbol[symbol],
  }));
}

const UNIVERSE = {
  mode: 'screened',
  preview: false,
  allowed_sectors: ['Technology', 'Industrials', 'Financials', 'Health Care', 'Consumer Discretionary'],
  fixed_symbols: [],
  held_symbols: ['MSFT', 'JPM'],
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
  pool: { size: 12, max: 150 },
  counts: { pool: 12, eligible: 7, ready: 1, excluded: 2, warming_up: 1, held: 2 },
  screen: {
    session_date: '2026-10-14',
    failed: false,
    equity: '10000.00',
    max_position_usd: '2036.00',
    funnel: [
      { key: 'pool', count: 12, out: [] },
      { key: 'tradable', count: 12, out: [] },
      { key: 'history', count: 11, out: ['ANET'] },
      { key: 'min_price', count: 11, out: [] },
      { key: 'one_share', count: 9, out: ['BKNG', 'AZO'] },
      { key: 'dollar_volume', count: 9, out: [] },
    ],
    eligible_total: 7,
  },
  sectors: [
    { sector: 'Technology', pool: 5, eligible: 4, rank: 2, rank_total: 5 },
    { sector: 'Industrials', pool: 2, eligible: 2, rank: 4, rank_total: 5 },
    { sector: 'Financials', pool: 1, eligible: 1, rank: 3, rank_total: 5 },
    { sector: 'Health Care', pool: 1, eligible: 1, rank: 5, rank_total: 5 },
    { sector: 'Consumer Discretionary', pool: 2, eligible: 0, rank: 1, rank_total: 5 },
  ],
  rows: poolRows(),
};

const SYNC = {
  jobs: [
    {
      job: 'daily_bars', label: 'Daily bars', status: 'ok',
      started_at: '2026-10-15T12:00:00Z', finished_at: '2026-10-15T12:00:38Z',
      duration_seconds: 38, done: 72, total: 72, failures: [],
      schedule: '08:00 NY · daily', late: false,
    },
    {
      job: 'screen', label: 'Daily screen', status: 'ok',
      started_at: '2026-10-15T12:10:00Z', finished_at: '2026-10-15T12:10:09Z',
      duration_seconds: 9, done: 72, total: 72, failures: [],
      schedule: '08:10 NY · trading days', late: false,
    },
    {
      job: 'bars_5m', label: '5-min bars', status: 'ok',
      started_at: '2026-10-15T16:43:00Z', finished_at: '2026-10-15T16:43:03Z',
      duration_seconds: 3.1, done: 71, total: 71, failures: [],
      schedule: 'each cycle', late: false,
    },
    {
      job: 'history', label: 'History for new symbols', status: 'running',
      started_at: '2026-10-15T16:40:00Z', finished_at: null,
      duration_seconds: null, done: 41, total: 72, failures: [],
      schedule: 'when a symbol is added', late: false,
    },
    {
      job: 'earnings', label: 'Earnings calendar', status: 'failed',
      started_at: '2026-10-15T12:00:00Z', finished_at: '2026-10-15T12:00:01Z',
      duration_seconds: 1.2, done: 71, total: 72,
      failures: [
        { what: 'ANET', error: 'Finnhub: no data for this symbol', next_retry: '2026-10-16T12:00:00' },
      ],
      schedule: '08:00 NY · daily', late: false,
    },
    {
      job: 'corporate_actions', label: 'Corporate actions', status: 'ok',
      started_at: '2026-10-15T12:00:00Z', finished_at: '2026-10-15T12:00:04Z',
      duration_seconds: 4, done: 1, total: null, failures: [],
      schedule: '08:00 NY · daily', late: false,
    },
    {
      job: 'fred', label: 'FRED (VIX and releases)', status: 'ok',
      started_at: '2026-10-15T12:00:00Z', finished_at: '2026-10-15T12:00:02Z',
      duration_seconds: 2, done: 2, total: 2, failures: [],
      schedule: '08:00 & 17:00 NY · daily', late: false,
    },
    {
      job: 'ecb', label: 'ECB rates', status: 'ok',
      started_at: '2026-10-15T12:00:00Z', finished_at: '2026-10-15T12:00:01Z',
      duration_seconds: 1, done: 1, total: null, failures: [],
      schedule: '08:00 NY · daily', late: false,
    },
  ],
  history: { ready: 41, total: 72, eta_seconds: 120 },
  max_requests: 20,
};

async function mockUniverse(page: Page, universe: object = UNIVERSE) {
  await page.route('**/api/universe**', (route) => {
    const url = route.request().url();
    const method = route.request().method();
    const json = (body: object) =>
      route.fulfill({ contentType: 'application/json', json: body });
    if (url.includes('/api/universe/sync/') && method === 'POST') {
      return json({ job: 'earnings', accepted: true });
    }
    if (url.includes('/api/universe/sync')) return json(SYNC);
    if (method === 'GET') return json(universe);
    return route.fallback();
  });
}

function shot(testInfo: { project: { name: string } }, name: string): string {
  return `${screenshotDir('077')}/${name}-${testInfo.project.name === 'mobile' ? '390' : '1440'}.png`;
}

test('universe 077: screened mode with Data sync open', async ({ page }, testInfo) => {
  await mockUniverse(page);
  await page.goto('/universe');
  // one mode line, six tiles, the sync card open with its reasons
  await expect(page.getByText('screened pool')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Data sync')).toBeVisible();
  await expect(page.getByText(/open: 1 failed · 1 running/)).toBeVisible();
  await expect(page.getByText(/Loading history · 41 of 72/).first()).toBeVisible();
  await expect(page.getByText(/ANET: Finnhub: no data/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  // pool table: status third, warming bars, ready and excluded rows
  await expect(page.getByText('180 of 260')).toBeVisible();
  await expect(page.getByText('name pending')).toBeVisible();
  await expect(page.getByText('Pool by sector')).toBeVisible();
  const width = page.viewportSize()?.width ?? 1440;
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(width + 1);
  await page.screenshot({ path: shot(testInfo, 'universe-077-screened'), fullPage: true });
});

test('universe 077: the remove question stays in the row', async ({ page }, testInfo) => {
  await mockUniverse(page);
  await page.goto('/universe');
  await expect(page.getByText('Data sync')).toBeVisible({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Remove BKNG from the pool' }).click();
  await expect(page.getByText('Remove BKNG from the pool?')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.screenshot({ path: shot(testInfo, 'universe-077-remove'), fullPage: true });
});

test('universe 077: fixed mode', async ({ page }, testInfo) => {
  const fixed = {
    ...UNIVERSE,
    mode: 'fixed',
    fixed_symbols: SYMBOLS.slice(0, 10).map(([symbol, name, sector]) => ({
      symbol, name, sector,
    })),
    held_symbols: ['MSFT', 'JPM'],
    preview: false,
  };
  await mockUniverse(page, fixed);
  await page.goto('/universe');
  await expect(page.getByText(/The universe is the fixed list below/)).toBeVisible({
    timeout: 60_000,
  });
  await page.screenshot({ path: shot(testInfo, 'universe-077-fixed'), fullPage: true });
});
