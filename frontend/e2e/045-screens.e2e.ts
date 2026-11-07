import path from 'node:path';

import { expect } from '@playwright/test';
import { screenshotDir, test } from './shots';

// 045 GUI catch-up screenshots → docs/screenshots/045/. Simulated mode,
// seeded fixtures (tests/e2e_seed.py).
const outDir = screenshotDir('045');
const suffix = (p: string) => (p === 'mobile' ? '390' : '1440');

async function shot(page: import('@playwright/test').Page, name: string) {
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

test('main', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Needs you/i })).toBeVisible({ timeout: 60_000 });
  await shot(page, `main-${suffix(testInfo.project.name)}.png`);
});

test('positions', async ({ page }, testInfo) => {
  await page.goto('/positions');
  await expect(page.getByText(/positions/i).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, `positions-${suffix(testInfo.project.name)}.png`);
});

test('reviews', async ({ page }, testInfo) => {
  await page.goto('/reviews');
  await expect(page.getByText(/review/i).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, `reviews-${suffix(testInfo.project.name)}.png`);
});

test('insights', async ({ page }, testInfo) => {
  await page.goto('/insights');
  await expect(page.getByText(/insight/i).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, `insights-${suffix(testInfo.project.name)}.png`);
});

test('inspector', async ({ page }, testInfo) => {
  await page.goto('/inspector');
  await expect(page.getByText(/inspector|model calls/i).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, `inspector-${suffix(testInfo.project.name)}.png`);
});

test('settings', async ({ page }, testInfo) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'API keys and data' })).toBeVisible({ timeout: 60_000 });
  await shot(page, `settings-${suffix(testInfo.project.name)}.png`);
});

test('universe', async ({ page }, testInfo) => {
  await page.goto('/universe');
  await expect(page.getByText(/universe/i).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, `universe-${suffix(testInfo.project.name)}.png`);
});

test('decision drawer', async ({ page, request }, testInfo) => {
  const list = await (await request.get('/api/decisions')).json();
  test.skip(!list.items?.length, 'no decisions in the seeded database');
  await page.goto(`/decisions/${list.items[0].id}`);
  await expect(page.getByText('What happened')).toBeVisible({ timeout: 60_000 });
  await shot(page, `decision-${suffix(testInfo.project.name)}.png`);
});

// 1024 (tablet) for Main, Positions and Settings.
for (const [route, name, waitFor] of [
  ['/', 'main', 'Needs you'],
  ['/positions', 'positions', 'positions'],
  ['/settings', 'settings', 'API keys and data'],
] as const) {
  test(`${name} — 1024`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop');
    await page.setViewportSize({ width: 1024, height: 900 });
    await page.goto(route);
    await expect(
      name === 'settings'
        ? page.getByRole('heading', { name: waitFor })
        : page.getByText(new RegExp(waitFor, 'i')).first(),
    ).toBeVisible({ timeout: 60_000 });
    await shot(page, `${name}-1024.png`);
  });
}
