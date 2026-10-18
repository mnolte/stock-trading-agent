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
  // The e2e database is shared between runs: put the mode back to fixed
  // before the fixed-mode checks (a pending screened version would win).
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

test('universe: screened mode shows the screen and the pool', async ({
  page,
  request,
}, testInfo) => {
  // The first screen seeds the 72-symbol pool and warms up 260 daily +
  // 60 sessions of 5-min bars per symbol with fakes — minutes, not seconds.
  testInfo.setTimeout(420_000);
  await setUniverseMode(request, 'screened');

  const screen = await request.post('/api/universe/screen', {
    data: {},
    timeout: 360_000,
  });
  expect(screen.ok(), await screen.text()).toBeTruthy();

  await page.goto('/universe', { waitUntil: 'domcontentloaded' });

  await expect(page.getByText('screened pool')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Pool of 72')).toBeVisible();
  await expect(
    page.getByText(/Screened at 08:10 New York \(14:10 Amsterdam\)/)
  ).toBeVisible();
  await expect(page.getByText('Today’s screen')).toBeVisible();
  await expect(page.getByText('In the pool')).toBeVisible();
  await expect(
    page.getByText('Eligible for the next session', { exact: true })
  ).toBeVisible();
  await expect(page.getByRole('tab', { name: /Eligible/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add to pool' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Import CSV…' })
  ).toBeVisible();
  await expect(page.getByText('eligible').first()).toBeVisible();

  await page.screenshot({ path: shot('universe-screened', testInfo.project.name), fullPage: true });
});

test('settings: universe mode and liquidity screen rows', async ({
  page,
  request,
}, testInfo) => {
  await setUniverseMode(request, 'screened');

  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' })
  ).toBeVisible({ timeout: 60_000 });

  await expect(page.getByRole('radio', { name: 'Fixed list' })).toBeVisible();
  await expect(
    page.getByRole('radio', { name: 'Screened pool' })
  ).toBeVisible();
  // The saved-but-pending switch stays visible: active value in the control,
  // pending value marked next to it and summarised in the banner. Other saves
  // from earlier specs share the pending version (the e2e database is shared
  // and no cycle promotes mid-run), so only require Universe mode in the list.
  await expect(page.getByText('Pending: Screened pool')).toBeVisible();
  await expect(
    page.getByText(/is pending — it applies at the next cycle.*Universe mode/)
  ).toBeVisible();
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

test.afterEach(async ({ request }) => {
  // Leave the shared e2e database in the v0.1 mode.
  await setUniverseMode(request, 'fixed');
});
