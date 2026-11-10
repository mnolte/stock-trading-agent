import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 060 — PR screenshots (docs/screenshots/060/): the VIX close beside the
// Regime label, the latest-decision chip, the two Insights slices and the
// FRED data-source tile. Run with SCREENSHOT_DIR=../docs/screenshots/060;
// a plain `make test` writes to a temp folder instead.

const outDir = screenshotDir('060');

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

test('dashboard header and latest decision', async ({ page }, testInfo) => {
  await page.goto('/');
  await settle(page);
  await shot(page, `dashboard-${TAG(testInfo.project.name)}.png`);
});

test('insights slices', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', '390 shot comes from the mobile project');
  await page.goto('/insights');
  await settle(page);
  await shot(page, 'insights-1440.png');
});

test('settings data sources', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', '390 shot comes from the mobile project');
  await page.goto('/settings/trading');
  await settle(page);
  // Scroll the data-source tiles into view for the shot.
  const sources = page.getByText('Data sources', { exact: true });
  if (await sources.count()) {
    await sources.first().scrollIntoViewIfNeeded();
  }
  await shot(page, 'settings-1440.png');
});
