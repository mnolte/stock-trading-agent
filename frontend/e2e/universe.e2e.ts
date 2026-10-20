import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// PR 11 screenshots land in docs/screenshots/pr-11/; design/ is untouched.
const outDir =
  process.env.SCREENSHOT_DIR ??
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../docs/screenshots/pr-11'
  );

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

/** Save a settings version (applies at the next screen for universe.*). */
async function setUniverseMode(
  request: import('@playwright/test').APIRequestContext,
  mode: 'fixed',
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
  await expect(page.getByText('AAPL')).toBeVisible();

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

  // Screened mode is blocked until PR 12: the app stays on the fixed list
  // and the screen results show as a preview only.
  await expect(page.getByText('fixed list', { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByText(/the screened pool arrives with PR 12/i)
  ).toBeVisible();
  await expect(page.getByText('Pool')).toBeVisible();
  await expect(page.getByText('Today’s screen')).toBeVisible();
  await expect(
    page.getByText(/a preview, not used by cycles yet/i)
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

test('settings: the screened pool is blocked until PR 12', async ({
  page,
  request,
}, testInfo) => {
  // The API refuses a screened-mode save outright.
  const refused = await request.post('/api/settings', {
    data: {
      settings: { universe: { mode: 'screened' } },
      reason: 'e2e: try screened mode',
    },
  });
  expect(refused.status()).toBe(422);
  expect(await refused.text()).toContain('Screened pool arrives with PR 12');

  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' })
  ).toBeVisible({ timeout: 60_000 });

  await expect(page.getByRole('radio', { name: 'Fixed list' })).toBeVisible();
  // The screened option is visible but disabled until PR 12.
  await expect(
    page.getByRole('radio', { name: 'Screened pool' })
  ).toBeDisabled();
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
});
