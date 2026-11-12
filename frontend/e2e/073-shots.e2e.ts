import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 073 — PR screenshots (docs/screenshots/073/): the dashboard at 1440,
// 1024 and 390 px with MSFT (held) and SPY (a no_call) selected. Run with
// SCREENSHOT_DIR=../docs/screenshots/073; a plain `make test` writes to a
// temp folder instead.

const outDir = screenshotDir('073');

async function settle(page: Page) {
  await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
  await page
    .locator('.sa-skel')
    .first()
    .waitFor({ state: 'hidden', timeout: 30_000 })
    .catch(() => {});
  await page.waitForTimeout(400);
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

for (const symbol of ['MSFT', 'SPY'] as const) {
  test(`dashboard ${symbol}`, async ({ page }, testInfo) => {
    const mobile = testInfo.project.name === 'mobile';
    if (!mobile) {
      // The 1024 shot comes from the desktop project at a narrower viewport.
      await page.setViewportSize({ width: 1024, height: 900 });
    }
    await page.goto(`/?symbol=${symbol}`);
    await settle(page);
    // Kickoff "Done when": no sideways page scroll at any width.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    // And no rendering glitches in the strip or cards.
    const bodyText = await page.locator('main').innerText();
    expect(bodyText).not.toMatch(/undefined|NaN/);
    if (mobile) {
      await shot(page, `dashboard-${symbol.toLowerCase()}-390.png`);
    } else {
      await shot(page, `dashboard-${symbol.toLowerCase()}-1024.png`);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.waitForTimeout(300);
      await shot(page, `dashboard-${symbol.toLowerCase()}-1440.png`);
    }
  });
}
