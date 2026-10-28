import path from 'node:path';

import { expect, type Locator, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 17 screenshots are for docs/screenshots/pr-17/ (pass SCREENSHOT_DIR,
// see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-17');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

// ---------------------------------------------------------------------------
// Fixtures — PR 17's scenarios are frontend behaviour, so the API responses
// are mocked (the spec's "via mocked responses") while the page runs against
// the real backend for everything else.
// ---------------------------------------------------------------------------

/** 71 tickers — the screened pool from the PR brief (MSFT + NVDA held). */
const SYMBOLS = [
  'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'AVGO', 'TSLA', 'JPM',
  'V', 'XOM', 'UNH', 'MA', 'COST', 'HD', 'PG', 'NFLX', 'CRM', 'AMD', 'ORCL',
  'BAC', 'KO', 'PEP', 'MRK', 'WMT', 'ABBV', 'CVX', 'MCD', 'CSCO', 'ACN',
  'LIN', 'ABT', 'ADBE', 'TMO', 'DIS', 'PM', 'DHR', 'INTC', 'TXN', 'VZ',
  'NKE', 'NEE', 'QCOM', 'IBM', 'UPS', 'BMY', 'CAT', 'GE', 'BA', 'SBUX',
  'AMGN', 'HON', 'LOW', 'GS', 'BLK', 'AXP', 'DE', 'PLD', 'MDLZ', 'SYK',
  'GILD', 'ADP', 'TJX', 'MMC', 'ISRG', 'C', 'MO', 'LMT', 'ELV', 'RTX',
  'SPGI',
];

function watchlistRow(symbol: string, rank: number) {
  return {
    symbol,
    price: '123.45',
    day_change_pct: 1.23,
    rules: { status: 'pass', reason: null, checks: [] },
    earnings_date: null,
    last_action: 'hold',
    last_action_time: '2026-10-15T14:46:00Z',
    rank,
    days_to_earnings: null,
  };
}

function screenedWatchlist() {
  return {
    computed_at: '2026-10-15T14:00:00Z',
    regime: null,
    universe_mode: 'screened',
    watchlist_rows: 15,
    earnings_blackout_days: 2,
    rows: SYMBOLS.map((s, i) => watchlistRow(s, i + 1)),
  };
}

function positionsJson() {
  return {
    open: [
      {
        id: 1,
        symbol: 'MSFT',
        qty: 4,
        avg_entry_price: '510.20',
        market_value: '2052.00',
        current_stop_price: '501.00',
        target_price: null,
        origin: 'entry',
        pending_ack: false,
        opened_at: '2026-10-15T13:36:00Z',
        closed_at: null,
        realized_pnl_raw: null,
        exit_reason: null,
        last_price: '513.00',
        unrealized_pnl: 11.2,
        unrealized_pnl_eur: null,
        r_multiple: null,
        stop_distance_label: '2.4%',
      },
      {
        id: 2,
        symbol: 'NVDA',
        qty: 2,
        avg_entry_price: '181.00',
        market_value: '365.40',
        current_stop_price: '177.50',
        target_price: null,
        origin: 'entry',
        pending_ack: false,
        opened_at: '2026-10-15T14:06:00Z',
        closed_at: null,
        realized_pnl_raw: null,
        exit_reason: null,
        last_price: '182.70',
        unrealized_pnl: 3.4,
        unrealized_pnl_eur: null,
        r_multiple: null,
        stop_distance_label: '1.9%',
      },
    ],
    closed_today: [],
    today: { entries: 0, exits: 0, round_trips: 0, reentry_blocked: [] },
  };
}

function feedItem(
  id: number,
  cycleId: number,
  cycleIndex: number,
  time: string,
  symbol: string,
  action: string,
  opts: { reason?: string | null; change?: boolean; called?: boolean } = {},
) {
  return {
    id,
    time,
    cycle_id: cycleId,
    cycle_index: cycleIndex,
    symbol,
    context: 'candidate',
    action,
    model_action: opts.called === false ? null : action,
    no_call_reason: action === 'no_call' ? (opts.reason ?? null) : null,
    reason: opts.reason ?? null,
    change: opts.change ?? false,
    called: opts.called ?? action !== 'no_call',
  };
}

/** A full-session feed: three cycles, each mixing changes and grouped rows. */
function fullFeed() {
  const items = [];
  let id = 100;
  // Cycle 3 (latest, stays open): an entry, a stop raise, holds and no-calls.
  items.push(feedItem(id++, 3, 3, '2026-10-15T18:46:00Z', 'AAPL', 'enter', { change: true }));
  items.push(
    feedItem(id++, 3, 3, '2026-10-15T18:46:00Z', 'MSFT', 'tighten_stop', { change: true }),
  );
  for (const s of ['NVDA', 'JPM', 'V', 'MA', 'COST', 'HD', 'PG', 'NFLX']) {
    items.push(feedItem(id++, 3, 3, '2026-10-15T18:46:00Z', s, 'hold', { reason: 'trend intact' }));
  }
  for (const s of ['CRM', 'AMD', 'ORCL', 'BAC', 'KO', 'PEP', 'MRK', 'WMT', 'ABBV', 'CVX', 'MCD', 'CSCO']) {
    items.push(
      feedItem(id++, 3, 3, '2026-10-15T18:46:00Z', s, 'no_call', {
        reason: 'model call cap reached',
        called: false,
      }),
    );
  }
  // Cycle 2 (collapsed): an exit plus grouped holds/skips/no-calls.
  items.push(feedItem(id++, 2, 2, '2026-10-15T17:46:00Z', 'JPM', 'exit', { change: true, reason: 'stop hit' }));
  for (const s of ['ACN', 'LIN', 'ABT', 'ADBE', 'TMO']) {
    items.push(feedItem(id++, 2, 2, '2026-10-15T17:46:00Z', s, 'skip', { reason: 'low confidence' }));
  }
  for (const s of ['DIS', 'PM', 'DHR', 'INTC', 'TXN', 'VZ', 'NKE', 'NEE', 'QCOM', 'IBM', 'UPS', 'BMY', 'CAT', 'GE', 'BA']) {
    items.push(
      feedItem(id++, 2, 2, '2026-10-15T17:46:00Z', s, 'no_call', {
        reason: 'below minimum price',
        called: false,
      }),
    );
  }
  // Cycle 1 (collapsed): no-calls only.
  for (const s of ['SBUX', 'AMGN', 'HON', 'LOW', 'GS', 'BLK', 'AXP', 'DE', 'PLD', 'MDLZ', 'SYK', 'GILD', 'ADP', 'TJX', 'MMC', 'ISRG', 'C', 'MO', 'LMT', 'ELV']) {
    items.push(
      feedItem(id++, 1, 1, '2026-10-15T16:46:00Z', s, 'no_call', {
        reason: 'below minimum price',
        called: false,
      }),
    );
  }
  return items;
}

function latestDecisionJson(inSession = true) {
  return {
    id: 901,
    symbol: 'MSFT',
    context: 'position',
    model_action: 'hold',
    final_action: 'hold',
    no_call_reason: null,
    confidence: 0.62,
    why: 'trend intact',
    model: 'gpt-5-mini',
    regime: null,
    earnings: null,
    sig: [],
    box: [],
    checks: [],
    price: '513.00',
    time: '2026-10-16T19:46:00Z',
    in_session: inSession,
  };
}

function dashboardJson(over: Record<string, unknown> = {}) {
  return {
    session: {
      state: 'open',
      next_cycle: '2026-10-15T19:46:00Z',
      next_open: null,
      next_close: '2026-10-15T20:00:00Z',
      shown_date: '2026-10-15',
      shown_is_today: true,
    },
    cycle_line: null,
    cycle_counter: null,
    stats: {
      positions: 2,
      positions_cap: 8,
      cash: '5000.00',
      since_start_pct: 1.23,
      since_label: 'since 5 Oct',
      spy_pct: 0.8,
      execution_mode: 'direct',
      calls_today: 12,
      calls_max: 60,
      calls_per_cycle: 3,
      cycles_today: 6,
      calls_cost_today: '0.012',
      calls_cost_month: '0.34',
      reviews_cost_month: null,
      reviews_cost_cap: '2.00',
      shadows_cost_month: null,
      shadows_cost_cap: '2.00',
    },
    briefing: { enabled: false, next_at: null, card: null },
    latest_decision: latestDecisionJson(),
    approvals_pending: 0,
    market_closed: null,
    feed: [],
    ...over,
  };
}

function chartJson(symbol: string, price: string) {
  const bars = [1, 2, 3].map((h) => ({
    time: `2026-10-15T${13 + h}:00:00Z`,
    session: '2026-10-15',
    open: price,
    high: price,
    low: price,
    close: price,
    volume: 1000,
    ema20: null,
    ema50: null,
    vwap: null,
  }));
  return {
    symbol,
    sessions: ['2026-10-15'],
    bars,
    levels: { kind: null },
    warming_up: false,
    price,
    day_change_pct: 1.23,
  };
}

const emptyApprovals = {
  mode: 'off',
  kill_switch_on: false,
  paused: false,
  next_cycle: null,
  items: [],
  recent: [],
};

async function mockApi(page: Page, path: string, json: unknown) {
  await page.route(`**/api/${path}`, (route) =>
    route.fulfill({ contentType: 'application/json', json }),
  );
}

/** Match the backend prefix on the URL path only — a `**`/api/`**` glob
 * would also catch the Vite source modules under /src/api/. */
const apiUrl = (url: URL) => url.pathname.startsWith('/api/');

// ---------------------------------------------------------------------------
// Dashboard at scale — the 71-symbol screened pool.
// ---------------------------------------------------------------------------

test('scale: 71-symbol pool — held picks + Find symbol, feed in collapsed cycles', async ({
  page,
}, testInfo) => {
  await mockApi(page, 'watchlist', screenedWatchlist());
  await mockApi(page, 'positions', positionsJson());
  await mockApi(page, 'approvals', emptyApprovals);
  await mockApi(page, 'dashboard**', dashboardJson({ feed: fullFeed() }));
  await page.route('**/api/chart/*', (route) =>
    route.fulfill({
      contentType: 'application/json',
      json: chartJson(route.request().url().split('/').pop() ?? '', '123.45'),
    }),
  );

  await page.goto('/');

  // No wall of one button per symbol: the chart header holds one button per
  // held position plus Find symbol — three buttons, not 71.
  const pickGroup = page.getByRole('group', { name: 'Pick a symbol' });
  await expect(pickGroup.getByRole('button')).toHaveCount(3);
  await expect(
    pickGroup.getByRole('button', { name: /MSFT/ }),
  ).toBeVisible();
  await expect(
    pickGroup.getByRole('button', { name: 'Find symbol' }),
  ).toBeVisible();

  // Find symbol: listbox over held + watchlist, filtered by typing; the
  // choice lands in ?symbol=.
  await pickGroup.getByRole('button', { name: 'Find symbol' }).click();
  const dialog = page.getByRole('dialog', { name: 'Find symbol' });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('listbox', { name: 'Symbols' }).getByRole('option'),
  ).toHaveCount(71);
  await dialog.getByLabel('Find a symbol').fill('AAPL');
  await expect(dialog.getByRole('option')).toHaveCount(1);
  await dialog.getByRole('option', { name: 'AAPL' }).click();
  await expect(page).toHaveURL(/symbol=AAPL/);
  await expect(
    page.getByRole('heading', { name: 'AAPL', exact: true }),
  ).toBeVisible();

  // Feed: grouped per cycle; the latest cycle is open, earlier cycles show
  // their changes only.
  await expect(
    page.getByRole('heading', { name: "Today's decisions" }),
  ).toBeVisible();
  const headers = page.locator('button[aria-expanded]').filter({
    hasText: 'cycle',
  });
  await expect(headers).toHaveCount(3);
  await expect(
    page.getByRole('button', { name: /cycle 2 · 1 change · 20 more decisions/ }),
  ).toBeVisible();
  // Collapsed cycle 2 still shows its change (the JPM exit) but hides the
  // grouped skips and no-calls.
  await expect(page.getByRole('link', { name: /EXIT.*JPM/ })).toBeVisible();
  await expect(page.getByText('ACN')).toHaveCount(0);
  // In the open cycle the 12 no-calls collapse into one grouped row with a
  // count and "+4 more" past the 8-name limit.
  const noCallRow = page.getByRole('link', { name: /NO CALL/ });
  await expect(noCallRow).toHaveCount(1);
  await expect(noCallRow.getByText('+4 more')).toBeVisible();
  // Expanding cycle 2 reveals its grouped rows.
  await page
    .getByRole('button', { name: /cycle 2 · 1 change · 20 more decisions/ })
    .click();
  await expect(page.getByRole('link', { name: /SKIP.*ACN/ })).toBeVisible();
  await expect(page.getByText('below minimum price').first()).toBeVisible();
  // A grouped row opens the LLM inspector filtered to cycle + action.
  await page.getByRole('link', { name: /NO CALL.*15|NO CALL/ }).first().click();
  await expect(page).toHaveURL(/\/inspector\?cycle=.*action=/);

  // Watchlist: screened mode shows the rank column and truncates to the
  // configured 15 rows behind Show all.
  await page.goto('/');
  await expect(page.getByText('Sorted by rank · 15 rows, then Show all')).toBeVisible();
  await expect(page.getByText('Rank', { exact: true })).toBeVisible();
  const rows = page.locator('[aria-label="Watchlist"] button.sa-row');
  await expect(rows).toHaveCount(15);
  await page.getByRole('button', { name: /Show all \(56 more\)/ }).click();
  await expect(rows).toHaveCount(71);

  await page.screenshot({ path: shot('main', testInfo.project.name), fullPage: true });
});

// ---------------------------------------------------------------------------
// Off-hours — a Saturday render shows Friday's session.
// ---------------------------------------------------------------------------

test('off-hours: the dashboard shows the last session, not an empty today', async ({
  page,
}, testInfo) => {
  await mockApi(page, 'approvals', emptyApprovals);
  await mockApi(
    page,
    'dashboard**',
    dashboardJson({
      session: {
        state: 'closed',
        next_cycle: '2026-10-19T13:40:00Z',
        next_open: '2026-10-19T13:30:00Z',
        next_close: null,
        shown_date: '2026-10-16',
        shown_is_today: false,
      },
      cycle_counter: {
        kill_switch: false,
        running: false,
        now: '2026-10-17T12:00:00Z',
        session_is_today: false,
        next_cycle: '2026-10-19T13:40:00Z',
        next_cycle_today: false,
        next_check: null,
        next_check_today: false,
        segments: [
          { time: '2026-10-16T13:36:00Z', status: 'done' },
          { time: '2026-10-16T14:46:00Z', status: 'done' },
        ],
        cycle_index: 6,
        cycles_today: 6,
        run_cycle_number: 42,
      },
      latest_decision: latestDecisionJson(false),
      market_closed: {
        next_open: '2026-10-19T13:30:00Z',
        first_cycle: '2026-10-19T13:40:00Z',
        stops: [{ symbol: 'MSFT', stop: '512.40' }],
        open_count: 1,
        today_summary: '2 entries · 4 stops raised',
      },
      feed: [
        feedItem(300, 6, 6, '2026-10-16T19:46:00Z', 'MSFT', 'hold', { reason: 'trend intact' }),
      ],
    }),
  );

  await page.goto('/');

  // The feed and the model-call tile are dated, not "today".
  await expect(
    page.getByRole('heading', { name: 'Friday 16 Oct · last session' }),
  ).toBeVisible();
  await expect(page.getByText('Model calls · Fri 16 Oct')).toBeVisible();

  // Market-closed card: next open, first cycle, stops and the Friday summary.
  await expect(
    page.getByRole('heading', { name: 'Market closed' }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /next session opens Monday 19 October at 15:30 Amsterdam \(09:30 New York\)/,
    ),
  ).toBeVisible();
  await expect(page.getByText(/first cycle runs at 15:40/)).toBeVisible();
  await expect(page.getByText('MSFT stop 512.40')).toBeVisible();
  await expect(
    page.getByText('Fri 16 Oct: 2 entries · 4 stops raised'),
  ).toBeVisible();

  // Cycle strip: the last session's cycles, no "today", and a countdown
  // longer than a day shows day/time only — never "in 64:12".
  await expect(page.getByText('Cycle 6 of 6', { exact: true })).toBeVisible();
  await expect(page.getByText(/next cycle Mon .* NY/)).toBeVisible();
  await expect(page.getByText(/\bin \d+:\d{2}\b/)).toHaveCount(0);

  // The latest decision is from Friday — the compact dated card.
  await expect(
    page.getByRole('heading', { name: 'Latest decision · MSFT' }),
  ).toBeVisible();
  await expect(page.getByText(/HOLD · Fri 16 Oct/)).toBeVisible();

  await page.screenshot({
    path: shot('states', testInfo.project.name),
    fullPage: true,
  });
});

test('states gallery page', async ({ page }, testInfo) => {
  await page.goto('/states');
  await expect(
    page.getByRole('heading', { name: 'States & dialogs', level: 1 }),
  ).toBeVisible();
  await expect(page.getByText('Market closed · Dashboard')).toBeVisible();
  await page.screenshot({
    path: shot('gallery', testInfo.project.name),
    fullPage: true,
  });
});

// ---------------------------------------------------------------------------
// Card states — skeleton, error + Retry, empty only after a successful load.
// ---------------------------------------------------------------------------

test('card states: error shows what failed and Retry recovers', async ({
  page,
}) => {
  // Fail every call until Retry is clicked — live-update refetches must not
  // consume the "good" response before the assertion runs.
  let failing = true;
  await page.route('**/api/watchlist', (route) => {
    if (failing) {
      return route.fulfill({ status: 500, body: 'broken' });
    }
    return route.fulfill({
      contentType: 'application/json',
      json: { computed_at: null, regime: null, rows: [] },
    });
  });

  await page.goto('/');

  // The watchlist card says what failed, keeps a Retry and shows no
  // premature empty text.
  await expect(page.getByText(/Couldn’t load the watchlist/)).toBeVisible();
  const retry = page
    .locator('section')
    .filter({ hasText: 'Couldn’t load the watchlist' })
    .getByRole('button', { name: 'Retry' });
  failing = false;
  await retry.click();

  // The retried empty response shows the designed empty copy.
  await expect(
    page.getByText(/No symbols on the watchlist yet/),
  ).toBeVisible();
});

test('card states: a slow first load shows a skeleton, never empty text', async ({
  page,
}) => {
  let release: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route('**/api/watchlist', async (route) => {
    await gate;
    await route.fulfill({
      contentType: 'application/json',
      json: { computed_at: null, regime: null, rows: [] },
    });
  });

  await page.goto('/');
  await expect(page.locator('.sa-skel').first()).toBeVisible();
  // While the first load is in flight there is no empty text.
  await expect(page.getByText(/No symbols on the watchlist/)).toHaveCount(0);
  release!();
});

test('backend down: page banner, Retry now, recovery without reload', async ({
  page,
}) => {
  // PR 25: an outage BEFORE the first load shows the "isn't answering"
  // page (covered in pr25.e2e.ts); the banner is for outages after a
  // successful load — so load first, then cut the API.
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: /decisions|last session/ }),
  ).toBeVisible();
  await page.route(apiUrl, (route) => route.abort());
  // Force one API call — waiting on a poll is timing-dependent in CI.
  await page.evaluate(() => fetch('/api/header').catch(() => undefined));

  await expect(
    page.getByText(/Can’t reach the Stock Trading Agent backend/),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Retrying every 15 seconds/)).toBeVisible();
  // The banner's Retry now — the page can hold a card-level retry too.
  const retryNow = page.getByRole('button', { name: 'Retry now' }).first();
  await expect(retryNow).toBeVisible();

  await page.unroute(apiUrl);
  await retryNow.click();
  await expect(
    page.getByText(/Can’t reach the Stock Trading Agent backend/),
  ).toHaveCount(0, { timeout: 30_000 });
  await expect(
    page.getByRole('heading', { name: /decisions|last session/ }),
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Latest request wins — rapid symbol switching shows the last symbol's chart.
// ---------------------------------------------------------------------------

test('races: a slow first chart response cannot overwrite a later pick', async ({
  page,
}) => {
  await mockApi(page, 'positions', positionsJson());
  await mockApi(page, 'approvals', emptyApprovals);
  // MSFT's chart takes 1.5 s; NVDA answers immediately. The MSFT response
  // lands last and must be dropped — the chart stays on NVDA.
  let msftServed = false;
  await page.route('**/api/chart/*', async (route) => {
    const symbol = route.request().url().split('/').pop() ?? '';
    if (symbol === 'MSFT') {
      await new Promise((r) => setTimeout(r, 1500));
      msftServed = true;
    }
    await route.fulfill({
      contentType: 'application/json',
      json: chartJson(symbol, symbol === 'MSFT' ? '111.00' : '999.00'),
    });
  });

  await page.goto('/');
  // First held symbol MSFT starts loading (slow); switch to NVDA at once.
  const pickGroup = page.getByRole('group', { name: 'Pick a symbol' });
  await expect(
    pickGroup.getByRole('button', { name: /MSFT/ }),
  ).toBeVisible();
  await pickGroup.getByRole('button', { name: /NVDA/ }).click();
  await expect(page).toHaveURL(/symbol=NVDA/);
  await expect(
    page.getByRole('heading', { name: 'NVDA', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('$999.00')).toBeVisible();
  // Wait until MSFT's late response was actually served — a fixed sleep
  // could pass before it lands and never exercise the race. Then give a
  // buggy overwrite a moment to render before asserting it didn't.
  await expect.poll(() => msftServed, { timeout: 15_000 }).toBe(true);
  await page.waitForTimeout(400);
  await expect(page.getByText('$999.00')).toBeVisible();
  await expect(page.getByText('$111.00')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'NVDA', exact: true }),
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Toasts — one per event across channels; info expires, critical persists.
// ---------------------------------------------------------------------------

function notificationsFixture(severity: string, subject: string) {
  return {
    id: 999_001,
    time: new Date().toISOString(),
    event: severity === 'critical' ? 'kill_switch' : 'test',
    severity,
    subject,
    body: 'One notification event, sent on every channel.',
    link: null,
    channels: ['app', 'browser', 'email'],
    channel_status: { app: 'sent', browser: 'sent', email: 'sent' },
    error: null,
  };
}

/** Mock the notifications GET so no other suite's events leak into the
 * toast assertions; the real POST /api/notifications/test still drives the
 * websocket wake-up that triggers the refetch. */
async function mockNotifications(
  page: Page,
  item: ReturnType<typeof notificationsFixture>,
) {
  let seeded = false;
  await page.route('**/api/notifications**', (route) => {
    if (!seeded) {
      seeded = true;
      return route.fulfill({
        contentType: 'application/json',
        json: { items: [], latest_id: item.id - 1 },
      });
    }
    return route.fulfill({
      contentType: 'application/json',
      json: { items: [item], latest_id: item.id },
    });
  });
}

/** The toast-expiry timers run on the page's fake clock so the 10 s
 * waits cost no wall time. The WS wake-up refetch is debounced (300 ms,
 * also fake) — advance the clock until the toast has actually mounted. */
async function tickUntilMounted(page: Page, toast: Locator) {
  await expect(async () => {
    await page.clock.runFor(500);
    expect(await toast.count()).toBe(1);
  }).toPass({ timeout: 30_000, intervals: [200] });
}

test('toasts: one toast per event, info closes after 10 s, hover pauses', async ({
  page,
  request,
}) => {
  await mockNotifications(
    page,
    notificationsFixture('info', '[Stock Trading Agent · PAPER] This is a test notification'),
  );
  await page.clock.install();
  await page.goto('/');
  await expect(page.locator('.sa-skel').first()).toBeHidden({
    timeout: 60_000,
  });

  // "Send test" publishes the notifications topic; the refetch then finds
  // one notification that was sent on every channel (app, browser, email) —
  // it must raise exactly one toast.
  await request.post('/api/notifications/test');
  const region = page.getByRole('region', { name: 'Notifications' });
  const toast = region.getByRole('status');
  await tickUntilMounted(page, toast);
  await expect(toast.getByText(/test notification/i)).toBeVisible();
  await expect(toast.getByText('closes in 10 s')).toBeVisible();

  // Hover pauses the expiry: still there past the 10 s mark.
  await toast.hover();
  await page.clock.runFor(11_000);
  await expect(toast).toBeVisible();
  await page.mouse.move(10, 10);
  await page.clock.runFor(15_000);
  await expect(toast).toBeHidden();
});

test('toasts: a critical notification stays until dismissed (role alert)', async ({
  page,
  request,
}) => {
  // The GET is mocked: seed empty, then the WS wake-up refetches and finds
  // one critical row. The real send-test POST still drives the wake-up.
  await mockNotifications(
    page,
    notificationsFixture('critical', '[Stock Trading Agent · PAPER] Kill switch turned on'),
  );

  await page.clock.install();
  await page.goto('/');
  await expect(page.locator('.sa-skel').first()).toBeHidden({
    timeout: 60_000,
  });
  await request.post('/api/notifications/test');

  const region = page.getByRole('region', { name: 'Notifications' });
  const alert = region.getByRole('alert');
  await tickUntilMounted(page, alert);
  await expect(alert.getByText('Kill switch turned on')).toBeVisible();
  // Past the info timeout it is still there — it only leaves on Dismiss.
  await page.clock.runFor(11_000);
  await expect(alert).toBeVisible();
  await alert.getByRole('button', { name: 'Dismiss' }).click();
  await expect(alert).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Keyboard walkthrough — every Tab lands on a control with a visible ring;
// on the mobile viewport the sidebar is a dialog with Escape + focus return.
// ---------------------------------------------------------------------------

// Walk the whole tab order: every landed control must show a visible ring.
// Stops when focus leaves the page (body) or wraps to a seen element — the
// tabbable count differs between desktop and mobile, so no fixed step count.
async function expectVisibleFocus(page: Page) {
  const seen = new Set<string>();
  for (let i = 0; i < 80; i += 1) {
    await page.keyboard.press('Tab');
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body)
        return { ok: false, who: 'body', key: 'body' };
      const style = getComputedStyle(el);
      const path: number[] = [];
      let node: Element | null = el;
      while (node && node !== document.body) {
        path.unshift(
          node.parentElement
            ? Array.prototype.indexOf.call(node.parentElement.children, node)
            : 0,
        );
        node = node.parentElement;
      }
      return {
        ok: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0,
        who: `${el.tagName} "${(el.textContent ?? '').slice(0, 40)}" label=${el.getAttribute('aria-label') ?? ''} cls=${el.getAttribute('class') ?? ''}`,
        outline: `${style.outlineStyle} ${style.outlineWidth}`,
        key: path.join('.'),
      };
    });
    if (info.who === 'body' || seen.has(info.key)) break;
    expect(
      info.ok,
      `Tab ${i + 1}: focused element has a visible ring — ${JSON.stringify(info)}`,
    ).toBeTruthy();
    seen.add(info.key);
  }
  expect(seen.size, 'no focusable element found on the page').toBeGreaterThan(0);
}

test('keyboard: dashboard, settings and strategies keep a visible focus ring', async ({
  page,
}, testInfo) => {
  await page.goto('/');
  // Wait for interactive content, not just the card labels — on the mobile
  // viewport the tab order is header + content only (no sidebar links), so
  // a still-loading dashboard would run out of focusables.
  await expect(
    page.getByRole('link', { name: 'Positions, open Positions and orders' }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveTitle(/Stock Trading Agent — Dashboard/);
  await expectVisibleFocus(page);

  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveTitle(/Stock Trading Agent — Settings/);
  await expectVisibleFocus(page);

  await page.goto('/strategies');
  await expect(
    page.getByRole('heading', { name: 'Compare', exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveTitle(/Stock Trading Agent — Strategies/);
  await expectVisibleFocus(page);

  // Mobile: the sidebar opens as a dialog — Escape closes it and focus
  // returns to the Menu button.
  if (testInfo.project.name === 'mobile') {
    await page.goto('/');
    const menu = page.getByRole('button', { name: 'Menu' });
    await menu.click();
    const dialog = page.getByRole('dialog', { name: 'Menu' });
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(menu).toBeFocused();
  }
});
