import { expect } from '@playwright/test';

import { test } from './shots';

// 056 step 4 — nothing scrolls sideways at phone width. Every route is
// opened at 390x844 (the mobile project) on the seeded app and the
// document must not be wider than the viewport.

const ROUTES = [
  '/',
  '/positions',
  '/performance',
  '/insights',
  '/inspector',
  '/reviews',
  '/strategies',
  '/backtest',
  '/readiness',
  '/settings/keys',
  '/settings/model',
  '/settings/notifications',
  '/universe',
  '/logs',
  '/states',
];

for (const route of ROUTES) {
  test(`no horizontal scroll at 390px: ${route}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'phone-width check');
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
    // Let the seeded data render before measuring.
    await page.locator('.sa-skel').first().waitFor({ state: 'hidden', timeout: 30_000 }).catch(() => {});
    await page.waitForTimeout(400);
    const width = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    expect(width, `${route} scrolls sideways at 390px`).toBe(390);
  });
}
