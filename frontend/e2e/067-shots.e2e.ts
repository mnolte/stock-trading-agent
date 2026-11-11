import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 067 — PR screenshots (docs/screenshots/067/): the next-release line in
// the cycle counter, the Settings → Entry rules group, and the
// release-next-morning chip on positions (mocked /api/positions, like
// 046-position-states). The dashboard and Settings shots run on the real
// e2e server — e2e_seed.py adds the CPI/PCE calendar rows.

const outDir = screenshotDir('067');

const TAG = (project: string) => (project === 'mobile' ? '390' : '1440');

async function shot(page: Page, name: string, fullPage = true) {
  await page.screenshot({ path: path.join(outDir, name), fullPage });
}

async function settle(page: Page) {
  await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
  await page
    .locator('.sa-skel')
    .first()
    .waitFor({ state: 'hidden', timeout: 30_000 })
    .catch(() => {});
  await page.waitForTimeout(400);
}

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

test('dashboard next-release line', async ({ page }, testInfo) => {
  await page.goto('/');
  await settle(page);
  // CPI is seeded for the frozen clock's next session. The counter's
  // second line compares the stored NY date with the real browser clock,
  // so the day word depends on when the spec runs ("today" once the
  // seeded date is in the past, "tomorrow" on the day before it).
  await expect(page.getByText(/CPI (today|tomorrow)/).first()).toBeVisible({
    timeout: 15_000,
  });
  await shot(page, `dashboard-${TAG(testInfo.project.name)}.png`);
});

test('settings entry rules', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name === 'mobile',
    '390 shot comes from the mobile project',
  );
  await page.goto('/settings/rules');
  await settle(page);
  const group = page.getByText('Economic releases', { exact: true });
  await expect(group).toBeVisible({ timeout: 15_000 });
  await group.scrollIntoViewIfNeeded();
  await shot(page, 'settings-1440.png');
});

test('position release chip', async ({ page }, testInfo) => {
  // The catch-all 503 keeps the client polling (and logging) every second;
  // on the 390px project the full-page screenshot needs more than the
  // default budget once the settle + chip waits have spent theirs.
  test.setTimeout(240_000);
  await mockBackground(page);
  await page.route('**/api/positions', (route) =>
    route.fulfill({
      json: {
        open: [
          {
            id: 1,
            symbol: 'MSFT',
            qty: 4,
            avg_entry_price: '418.40',
            market_value: '1673.60',
            current_stop_price: '409.60',
            target_price: '435.40',
            origin: 'model',
            pending_ack: false,
            opened_at: '2026-10-14T14:35:00Z',
            closed_at: null,
            realized_pnl_raw: null,
            exit_reason: null,
            last_price: '420.10',
            unrealized_pnl: 6.80,
            release_dates: [
              {
                type: 'cpi',
                label: 'CPI',
                date: '2026-10-16',
                time: '08:30',
                at: '2026-10-16T12:30:00Z',
              },
            ],
          },
        ],
        closed_today: [],
        today: {
          entries: 0,
          exits: 0,
          round_trips: 0,
          reentry_blocked: [],
        },
        account: {
          equity: '10120.55',
          cash: '9800.10',
          positions_value: '1673.60',
          equity_eur: null,
          eur_usd_rate: null,
          eur_usd_date: null,
          margin: '0',
          account_number: null,
        },
        max_open_positions: 4,
      },
    })
  );
  await page.goto('/positions');
  await settle(page);
  await expect(page.getByText(/CPI .*16 Oct.* 08:30 NY/)).toBeVisible({
    timeout: 15_000,
  });
  await shot(page, `positions-${TAG(testInfo.project.name)}.png`);
});
