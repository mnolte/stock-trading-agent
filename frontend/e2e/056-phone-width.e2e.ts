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
  test(`no sideways scroll, no console errors: ${route}`, async ({ page }, testInfo) => {
    // The review's script also recorded console errors on every route at
    // both widths (056 step 9) — collect them here on both projects.
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(String(err)));
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
    // Let the seeded data render before measuring.
    await page.locator('.sa-skel').first().waitFor({ state: 'hidden', timeout: 30_000 }).catch(() => {});
    await page.waitForTimeout(400);
    expect(consoleErrors, `${route} logged console errors`).toEqual([]);
    if (testInfo.project.name !== 'mobile') return;
    const width = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    expect(width, `${route} scrolls sideways at 390px`).toBe(390);
  });
}
