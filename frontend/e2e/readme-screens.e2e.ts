import path from 'node:path';

import { expect } from '@playwright/test';
import { screenshotDir, test } from './shots';

// `make readme-screens` images → docs/images/readme/ (it passes
// SCREENSHOT_DIR). A plain `make test` writes to a temp folder instead —
// committed images are never overwritten. One test per screen; every wait
// targets a stable element so the shot is deterministic.
const outDir = screenshotDir('readme');

async function shot(page: import('@playwright/test').Page, name: string, fullPage = true) {
  await page.screenshot({ path: path.join(outDir, name), fullPage });
}

test('dashboard (hero)', async ({ page }) => {
  await page.goto('/');
  const rows = page.locator('[aria-label="Watchlist"] button.sa-row');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });
  await shot(page, 'dashboard.png');
});

test('decision drawer (decision.v2)', async ({ page, request }) => {
  // The e2e seed writes one decision.v2 row; open its drawer.
  const list = await (await request.get('/api/decisions')).json();
  let id: number | null = null;
  for (const item of list.decisions ?? list.items ?? []) {
    const detail = await (await request.get(`/api/decisions/${item.id}`)).json();
    if ((detail.prompt_version ?? detail.decision?.prompt_version) === 'decision.v2') {
      id = item.id;
      break;
    }
  }
  test.skip(id === null, 'no decision.v2 row in the seeded database');
  await page.goto(`/decisions/${id}`);
  await expect(page.getByText('Options offered')).toBeVisible({ timeout: 60_000 });
  await shot(page, 'decision.png');
});

test('positions and orders', async ({ page }) => {
  await page.goto('/positions');
  await expect(page.getByRole('heading', { name: /open positions/i })).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, 'positions.png');
});

test('performance', async ({ page }) => {
  await page.goto('/performance');
  await expect(page.getByText('Equity vs SPY buy-and-hold')).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, 'performance.png');
});

test('insights', async ({ page }) => {
  await page.goto('/insights');
  await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, 'insights.png');
});

test('readiness', async ({ page }) => {
  await page.goto('/readiness');
  await expect(page.getByText('Not ready for live trading')).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, 'readiness.png');
});

test('LLM inspector', async ({ page }) => {
  await page.goto('/inspector');
  const rows = page.locator('[aria-label="Model calls"] button.sa-row');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });
  await shot(page, 'inspector.png');
});

test('settings', async ({ page }) => {
  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' }),
  ).toBeVisible({ timeout: 60_000 });
  await shot(page, 'settings.png');
});

test('sign in', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible({
    timeout: 60_000,
  });
  await shot(page, 'signin.png', false);
});

test('dashboard at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const rows = page.locator('[aria-label="Watchlist"] button.sa-row');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });
  await shot(page, 'dashboard-390.png');
});
