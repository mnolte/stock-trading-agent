import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 26 screenshots are for docs/screenshots/pr-26/ (pass SCREENSHOT_DIR,
// see ./shots), next to the design PNGs for the screens the day-trade-rule
// retirement touched: Positions (Today card), Main (Re-entry row),
// Settings (Earnings exit tomorrow), States (flatten dialog) and the
// decision drawer (Not traded today).
const outDir = screenshotDir('pr-26');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

async function mockApi(page: Page, api: string, json: unknown) {
  await page.route(`**/api/${api}`, (route) =>
    route.fulfill({ contentType: 'application/json', json }),
  );
}

const position = (over: Record<string, unknown>) => ({
  id: 1,
  symbol: 'MSFT',
  qty: 4,
  avg_entry_price: '418.40',
  market_value: '1672.80',
  current_stop_price: '409.60',
  target_price: '435.40',
  origin: 'agent',
  pending_ack: false,
  opened_at: '2026-10-15T14:48:00Z',
  closed_at: null,
  realized_pnl_raw: null,
  exit_reason: null,
  legs: [],
  last_price: '418.20',
  unrealized_pnl: -0.8,
  unrealized_pnl_eur: null,
  r_multiple: null,
  stop_distance_label: null,
  earnings_date: null,
  opened_today: true,
  ...over,
});

// MSFT opened today and still held; JPM was bought and sold in the same
// session; AMZN was closed today too — both are blocked from re-entry.
// (opened_today comes from the backend's clock, not the browser's.)
const positionsJson = {
  open: [
    position({}),
    position({
      id: 2,
      symbol: 'NVDA',
      qty: 2,
      avg_entry_price: '182.10',
      market_value: '365.40',
      opened_at: '2026-10-14T15:12:00Z',
      opened_today: false,
    }),
  ],
  closed_today: [
    position({
      id: 3,
      symbol: 'JPM',
      // Real-time opened_at: the GUI marks closed rows "opened today" from
      // the browser's clock, which the frozen e2e backend clock can't
      // match with a static timestamp.
      opened_at: new Date().toISOString(),
      closed_at: '2026-10-15T17:10:00Z',
      realized_pnl_raw: 12.4,
      exit_reason: 'target',
    }),
    position({
      id: 4,
      symbol: 'AMZN',
      opened_at: '2026-10-14T14:48:00Z',
      closed_at: '2026-10-15T16:31:00Z',
      realized_pnl_raw: -4.2,
      exit_reason: 'stop',
      opened_today: false,
    }),
  ],
  today: {
    entries: 2,
    exits: 2,
    round_trips: 1,
    reentry_blocked: ['AMZN', 'JPM'],
  },
  account: {
    equity: '10200.00',
    cash: '8400.00',
    positions_value: '2038.20',
    equity_eur: '8700.00',
    eur_usd_rate: '1.17',
    eur_usd_date: '2026-10-15',
    margin: 'none · cash only',
    account_number: 'PA3TEST123',
  },
  reconciliation: {
    ok: true,
    checked_at: '2026-10-15T16:58:00Z',
    stream_connected: true,
    account_number: 'PA3TEST123',
  },
  max_open_positions: 4,
};

const emptyOrders = { orders: [] };
const emptyApprovals = {
  mode: 'off',
  kill_switch_on: false,
  paused: false,
  next_cycle: null,
  items: [],
  recent: [],
};

test('positions: Today card replaces the day-trade boxes', async ({ page }, testInfo) => {
  await mockApi(page, 'positions', positionsJson);
  await mockApi(page, 'orders', emptyOrders);
  await mockApi(page, 'approvals', emptyApprovals);

  await page.goto('/positions');

  const today = page.locator('section').filter({ hasText: 'Not to be bought again today' });
  await expect(page.getByText('Entries', { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(today.getByText('2 · MSFT, JPM')).toBeVisible();
  await expect(today.getByText('2 · JPM, AMZN')).toBeVisible();
  await expect(today.getByText('AMZN, JPM')).toBeVisible();

  await page.screenshot({ path: shot('positions', testInfo.project.name), fullPage: true });
});

test('dashboard: risk budget shows the Re-entry row', async ({ page }, testInfo) => {
  await mockApi(page, 'positions', positionsJson);
  await mockApi(page, 'orders', emptyOrders);
  await mockApi(page, 'approvals', emptyApprovals);

  await page.goto('/');

  const card = page.locator('section[aria-label="Risk budget"]');
  await expect(card).toBeVisible({ timeout: 60_000 });
  await expect(card.getByText('Re-entry', { exact: true })).toBeVisible();
  await expect(card.getByText('AMZN · JPM')).toBeVisible();

  await page.screenshot({ path: shot('main', testInfo.project.name), fullPage: true });
});

test('decision drawer: re-entry gate reads "Not traded today"', async ({ page }, testInfo) => {
  await mockApi(page, 'decisions/426', {
    id: 426,
    cycle_time: '2026-10-15T14:46:00Z',
    symbol: 'MSFT',
    context: 'candidate',
    final_action: 'blocked',
    no_call_reason: null,
    input_snapshot: {},
    rules_result: null,
    model_output: null,
    validation_result: null,
    gates_result: {
      allowed: false,
      blocked_by: 'reentry',
      checks: [
        { name: 'reentry', passed: false, detail: 'sold earlier this session' },
      ],
    },
    trade: null,
    outcome: null,
    next_outcome_due: null,
    earnings: null,
    execution_mode: 'automatic',
    eur_usd_rate: null,
    prompt_version: 'decision.v1',
    prompt_hash: 'abc',
    prompt_text: null,
    model: 'gpt-test',
    tokens_in: null,
    tokens_out: null,
    tokens: null,
    cost: null,
    latency_ms: null,
    raw_response: null,
    orders: [],
    approval: null,
  });

  await page.goto('/decisions/426');

  await expect(page.getByRole('heading', { name: 'Safety gates' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Not traded today')).toBeVisible();
  await expect(page.getByText(/sold earlier this session/)).toBeVisible();

  await page.screenshot({ path: shot('decision', testInfo.project.name), fullPage: true });
});

test('settings: notifications list the earnings exit, no day-trade guard', async ({ page }, testInfo) => {
  await page.goto('/settings');
  await page.getByRole('link', { name: 'Notifications' }).first().click();

  await expect(page.getByText('Earnings exit tomorrow')).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Day-trade guard')).toHaveCount(0);

  await page.screenshot({ path: shot('settings', testInfo.project.name), fullPage: true });
});

test('states: flatten dialog has no day-trade warning', async ({ page }, testInfo) => {
  await page.goto('/states');

  await expect(
    page.getByRole('heading', { name: 'States & dialogs', level: 1 }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Day-trade guard')).toHaveCount(0);

  await page.getByRole('button', { name: 'Sell everything confirmation' }).click();
  const dialog = page.getByRole('dialog', { name: 'Sell everything now?' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/counts as a day trade/)).toHaveCount(0);

  await page.screenshot({ path: shot('states-flatten', testInfo.project.name) });
});
