import { expect, type Page } from '@playwright/test';

import { test } from './shots';

// 046 step 6 (G12): the card-level states the standard e2e seed can't
// reach — broker-stale positions, the no-stop chip and a held-back exit —
// rendered from a mocked /api/positions. Banner and Needs you kinds are
// covered by 046-states.e2e.ts.

async function mockBackground(page: Page) {
  await page.routeWebSocket('**/ws', (socket) => socket.close());
  await page.route(/\/api\//, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.fallback();
    if (path.endsWith('/auth/status'))
      return route.fulfill({ json: { required: false, signed_in: true } });
    if (path === '/api/banners') return route.fulfill({ json: { items: [] } });
    if (path === '/api/attention')
      return route.fulfill({ json: { items: [], count: 0, health: {} } });
    if (path === '/api/orders') return route.fulfill({ json: { orders: [] } });
    return route.fulfill({ status: 503, json: { detail: 'Isolated state test' } });
  });
}

function positionRow(extra: Record<string, unknown>) {
  return {
    id: 1,
    symbol: 'KO',
    qty: 7,
    avg_entry_price: '62.40',
    market_value: '441.84',
    current_stop_price: '59.10',
    target_price: '68.00',
    origin: 'model',
    pending_ack: false,
    opened_at: '2026-10-14T14:35:00Z',
    closed_at: null,
    realized_pnl_raw: null,
    exit_reason: null,
    last_price: '63.12',
    unrealized_pnl: 5.04,
    ...extra,
  };
}

function positionsBody(open: object[], extra: Record<string, unknown> = {}) {
  return {
    open,
    closed_today: [],
    today: { entries: 0, exits: 0, round_trips: 0, reentry_blocked: [] },
    account: {
      equity: '10120.55',
      cash: '9800.10',
      positions_value: '320.45',
      equity_eur: null,
      eur_usd_rate: null,
      eur_usd_date: null,
      margin: '0',
      account_number: null,
    },
    max_open_positions: 4,
    ...extra,
  };
}

test('positions: broker read timed out — stale note and values', async ({
  page,
}) => {
  await mockBackground(page);
  await page.route('**/api/positions', (route) =>
    route.fulfill({
      json: positionsBody([positionRow({})], {
        stale: true,
        as_of: '2026-10-15T19:58:00Z',
      }),
    })
  );
  await page.goto('/positions');
  await expect(page.getByText(/Alpaca not answering/i).first()).toBeVisible({ timeout: 15_000 });
  // Last reconciled values still render under the note.
  await expect(page.getByText('KO', { exact: false }).first()).toBeVisible();
});

test('positions: no stop at Alpaca chip and detail', async ({ page }) => {
  await mockBackground(page);
  await page.route('**/api/positions', (route) =>
    route.fulfill({
      json: positionsBody([
        positionRow({
          current_stop_price: null,
          stop_missing: {
            since: '2026-10-15T15:46:00Z',
            reason: 'broker rejected',
            stop_qty: '7',
          },
        }),
      ]),
    })
  );
  await page.goto('/positions');
  await expect(
    page.getByText(/no stop at Alpaca — broker rejected/i)
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('stop none', { exact: true })).toBeVisible();
});

test('positions: exit held back shows reason, tries and failure', async ({
  page,
}) => {
  await mockBackground(page);
  await page.route('**/api/positions', (route) =>
    route.fulfill({
      json: positionsBody([
        positionRow({
          pending_exit: {
            reason: 'model exit',
            decision_id: 42,
            since: '2026-10-15T19:40:00Z',
            failure: 'broker timeout',
            tries: 3,
          },
        }),
      ]),
    })
  );
  await page.goto('/positions');
  await expect(
    page.getByText(/exit held back since/i).first()
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/3 tries/)).toBeVisible();
  await expect(page.getByText(/broker timeout/).first()).toBeVisible();
});
