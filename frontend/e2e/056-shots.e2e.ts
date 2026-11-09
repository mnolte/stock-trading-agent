import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 056 step 9 — PR screenshots (docs/screenshots/056/). Run with
// SCREENSHOT_DIR=../docs/screenshots/056; a plain `make test` writes to a
// temp folder instead. Names carry the viewport: -1440 (desktop project),
// -390 (mobile project), -1024 (set per test).

const outDir = screenshotDir('056');

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

test('settings section', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', '390 shot comes from the mobile project');
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto('/settings/trading');
  await settle(page);
  await shot(page, 'settings-1024.png');
});

test('settings at project width', async ({ page }, testInfo) => {
  if (testInfo.project.name === 'desktop') {
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page.goto('/settings/trading');
  await settle(page);
  await shot(page, `settings-${TAG(testInfo.project.name)}.png`);
});

test('backtest results', async ({ page }, testInfo) => {
  await page.goto('/backtest');
  await settle(page);
  // The e2e seed leaves a `done` run; open its results like pr14 does.
  const runButton = page.getByRole('button', { name: /2026-09-14/ });
  await expect(runButton).toBeVisible({ timeout: 60_000 });
  await runButton.click();
  await expect(
    page.getByRole('columnheader', { name: /SPY buy and hold/ }),
  ).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(400);
  await shot(page, `backtest-${TAG(testInfo.project.name)}.png`);
});

test('performance', async ({ page }, testInfo) => {
  await page.goto('/performance');
  await settle(page);
  await expect(page.getByText('Equity vs').first()).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, `performance-${TAG(testInfo.project.name)}.png`);
});

test('insights', async ({ page }, testInfo) => {
  await page.goto('/insights');
  await settle(page);
  await expect(
    page.locator('main').getByRole('heading', { name: 'Insights', exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await shot(page, `insights-${TAG(testInfo.project.name)}.png`);
});

test('a card in Failed to draw', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'one card-failed shot is enough');
  // A malformed attention payload crashes only the Needs you card — its
  // items.map throws inside the CardBoundary, the rest of the dashboard
  // keeps rendering (056 finding 1).
  await page.route('**/api/attention**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      json: {
        items: 'broken',
        count: 1,
        health: {
          alpaca: { ok: true, at: '2026-10-15T14:46:00Z' },
          stream: 'connected',
          clock: { offset_ms: 12, level: 'ok' },
          keys: { status: 'ok', detail: 'all set' },
          email: 'ok',
        },
      },
    }),
  );
  await page.goto('/');
  await settle(page);
  await expect(
    page.getByText(/couldn.t be drawn\. The agent isn.t affected/).first(),
  ).toBeVisible({ timeout: 30_000 });
  await shot(page, 'card-failed-1440.png');
});

test('Needs you with an error group', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'one Needs-you shot is enough');
  // The seeded app has no unreviewed errors; mock the attention payload
  // with the shape attention.py emits for error fingerprints (056 step 2:
  // one-line summaries, count and last time).
  await page.route('**/api/attention**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      json: {
        items: [
          {
            kind: 'errors',
            severity: 'warning',
            text: '2 errors not reviewed.',
            detail:
              'Reviews screen failed to draw ×5 · last 07:30 · Alpaca order stream dropped ×2 · last 09:12 · Readiness waits for these.',
            action_label: 'Review in Logs',
            link: '/logs',
            created_at: '2026-10-15T14:46:00Z',
          },
        ],
        count: 1,
        health: {
          alpaca: { ok: true, at: '2026-10-15T14:46:00Z' },
          stream: 'connected',
          clock: { offset_ms: 12, level: 'ok' },
          keys: { status: 'ok', detail: 'all set' },
          email: 'ok',
        },
      },
    }),
  );
  await page.goto('/');
  await settle(page);
  await expect(page.getByText('errors not reviewed.')).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, 'needs-you-errors-1440.png');
});

function notificationItem(id: number, subject: string) {
  return {
    id,
    time: new Date().toISOString(),
    event: 'new_error',
    severity: 'critical',
    subject,
    body: 'Reviews screen failed to draw ×5 · last 07:30',
    link: '/logs',
    channels: ['app'],
    channel_status: { app: 'sent', browser: 'skipped', email: 'skipped' },
    error: null,
  };
}

test('dashboard at 390 with three toasts', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'phone-width shot');
  // Same wake-up path as 056-error-text: the GET seeds the cursor empty,
  // then the WS nudge refetches and three critical toasts stack.
  const items = [1, 2, 3].map((i) =>
    notificationItem(910_000 + i, `[Stock Trading Agent · PAPER] Error ${i}`),
  );
  let seeded = false;
  await page.route('**/api/notifications**', (route) => {
    if (!seeded) {
      seeded = true;
      return route.fulfill({
        contentType: 'application/json',
        json: { items: [], latest_id: items[0].id - 1 },
      });
    }
    return route.fulfill({
      contentType: 'application/json',
      json: { items, latest_id: items[2].id },
    });
  });
  await page.clock.install();
  await page.goto('/');
  await settle(page);
  await request.post('/api/notifications/test');
  const region = page.getByRole('region', { name: 'Notifications' });
  await expect(async () => {
    await page.clock.runFor(500);
    expect(await region.getByRole('alert').count()).toBe(3);
  }).toPass({ timeout: 30_000, intervals: [200] });
  // Viewport shot: the toasts overlay the bottom of the page.
  await shot(page, 'dashboard-390-toasts.png', false);
});
