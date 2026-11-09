import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// Screenshots for docs/screenshots/055/ (pass SCREENSHOT_DIR, see ./shots).
const outDir = screenshotDir('055');

// 055 → Loading: data cards show a delayed skeleton, never a bare
// "Loading…" label; a slow load says "Still loading · n s".

test('a held dashboard shows skeletons, not a loading label', async ({
  page,
}, testInfo) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/dashboard**', async (route) => {
    await gate;
    await route.continue();
  });

  await page.goto('/');

  // The skeleton appears after the 200 ms delay while the fetch is held.
  const skel = page.locator('.sa-skel').first();
  await expect(skel).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Loading…')).toHaveCount(0);
  await expect(page.getByText('Loading...')).toHaveCount(0);

  const name = testInfo.project.name === 'mobile' ? 'skeleton-390.png' : 'skeleton-1440.png';
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });

  release();
  await expect(skel).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator('.sa-skel')).toHaveCount(0, { timeout: 20_000 });
});

test('a slow dashboard says Still loading', async ({ page }) => {
  await page.route('**/api/dashboard**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 8_600));
    await route.continue();
  });

  await page.goto('/');

  await expect(page.getByText(/Still loading · \d+ s/).first()).toBeVisible({
    timeout: 20_000,
  });
});
