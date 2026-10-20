import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 12 screenshots are for docs/screenshots/pr-12/ (pass SCREENSHOT_DIR, see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-12');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

/** Save a settings version (applies at the next screen for universe.*). */
async function setUniverseMode(
  request: import('@playwright/test').APIRequestContext,
  mode: 'fixed' | 'screened',
) {
  const response = await request.post('/api/settings', {
    data: {
      settings: { universe: { mode } },
      reason: `e2e: universe mode ${mode}`,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

test.beforeEach(async ({ request }) => {
  // The e2e database is shared between runs: make sure the mode is fixed
  // before the fixed-mode checks.
  const universe = await request.get('/api/universe');
  if (universe.ok() && (await universe.json()).mode !== 'fixed') {
    await setUniverseMode(request, 'fixed');
  }
});

test('universe: fixed mode shows the v0.1 list and points to Settings', async ({
  page,
}, testInfo) => {
  await page.goto('/universe');

  await expect(
    page.getByRole('heading', { name: 'Universe', exact: true })
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('fixed list', { exact: true })).toBeVisible();
  await expect(page.getByText('Fixed list ·')).toBeVisible();
  await expect(
    page.getByText(/The fixed list is edited in/)
  ).toBeVisible();
  await expect(page.getByRole('link', { name: 'Screen settings' })).toBeVisible();
  // The preview pool table also lists AAPL — target the fixed-list chip.
  await expect(
    page.locator('b').filter({ hasText: 'AAPL' })
  ).toBeVisible();

  await page.screenshot({ path: shot('universe-fixed', testInfo.project.name), fullPage: true });
});

test('universe: the daily screen shows as a preview in fixed mode', async ({
  page,
  request,
}, testInfo) => {
  // The first screen seeds the 72-symbol pool and warms up 260 daily +
  // 60 sessions of 5-min bars per symbol with fakes — minutes, not seconds.
  testInfo.setTimeout(420_000);

  const screen = await request.post('/api/universe/screen', {
    data: {},
    timeout: 360_000,
  });
  expect(screen.ok(), await screen.text()).toBeTruthy();

  await page.goto('/universe', { waitUntil: 'domcontentloaded' });

  // Fixed mode keeps cycles on the configured list: the screen results
  // show as a preview until the screened pool is switched on.
  await expect(page.getByText('fixed list', { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByText(/the screened pool is switched on/i).first()
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Pool' })
  ).toBeVisible();
  await expect(page.getByText('Today’s screen')).toBeVisible();
  await expect(
    page.getByText(/a preview — cycles still trade the fixed list/i)
  ).toBeVisible();
  await expect(
    page.getByText('Eligible for the next session', { exact: true })
  ).toBeVisible();
  await expect(page.getByRole('tab', { name: /Eligible/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add to pool' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Import CSV…' })
  ).toBeVisible();

  await page.screenshot({ path: shot('universe-preview', testInfo.project.name), fullPage: true });
});

test('universe: a screened-mode screen becomes the list cycles trade', async ({
  page,
  request,
}, testInfo) => {
  // Reuses the warmed pool from the preview test when it ran first; on a
  // cold e2e database the first screen warms up — minutes, not seconds.
  testInfo.setTimeout(420_000);

  await setUniverseMode(request, 'screened');
  const screen = await request.post('/api/universe/screen', {
    data: {},
    timeout: 360_000,
  });
  expect(screen.ok(), await screen.text()).toBeTruthy();

  await page.goto('/universe', { waitUntil: 'domcontentloaded' });

  // The eligible list is now what cycles run — no preview wording.
  await expect(
    page.getByText('screened pool', { exact: true })
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Pool of \d+ · \d+ eligible/)).toBeVisible();
  await expect(
    page.getByText(/Cycles trade this list — the rank orders the model calls/i)
  ).toBeVisible();
  await expect(
    page.getByText(/a preview, not used by cycles yet|a preview — cycles still trade/i)
  ).toHaveCount(0);

  await page.screenshot({ path: shot('universe-screened', testInfo.project.name), fullPage: true });
});

test('settings: the screened pool can be switched on', async ({
  page,
  request,
}, testInfo) => {
  // The API accepts a screened-mode save (applies at the next screen).
  const saved = await request.post('/api/settings', {
    data: {
      settings: { universe: { mode: 'screened' } },
      reason: 'e2e: switch to the screened pool',
    },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();

  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' })
  ).toBeVisible({ timeout: 60_000 });

  // Both options are selectable. The save lands in a pending version —
  // the mode switch itself applies at the next daily screen.
  await expect(page.getByRole('radio', { name: 'Fixed list' })).toBeEnabled();
  await expect(
    page.getByRole('radio', { name: 'Screened pool' })
  ).toBeEnabled();
  await expect(page.getByText('Pending: Screened pool')).toBeVisible();
  await expect(page.getByText('Liquidity screen ·')).toBeVisible();
  await expect(page.getByLabel('Min price', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Min average dollar volume')).toBeVisible();
  await expect(page.getByLabel('Average over')).toBeVisible();
  await expect(page.getByLabel('Drop after')).toBeVisible();
  await expect(page.getByText('One share fits in max position')).toBeVisible();
  await expect(page.getByLabel('Model-call order')).toBeVisible();
  await expect(
    page.getByRole('link', { name: /Open Universe/ })
  ).toBeVisible();

  await page.screenshot({ path: shot('settings-universe', testInfo.project.name), fullPage: true });

  // Leave the shared e2e database on the fixed list for other tests:
  // the config save only takes effect at the next screen, so run one.
  testInfo.setTimeout(420_000);
  await setUniverseMode(request, 'fixed');
  const back = await request.post('/api/universe/screen', {
    data: {},
    timeout: 360_000,
  });
  expect(back.ok(), await back.text()).toBeTruthy();
});
