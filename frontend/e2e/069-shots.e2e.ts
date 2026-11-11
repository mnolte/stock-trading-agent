import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 069 Settings UX screenshots → docs/screenshots/069/ (SCREENSHOT_DIR).
// Runs against the simulated backend, so the risk preview endpoint
// answers with the seeded equity.

const outDir = screenshotDir('069');

async function shot(page: import('@playwright/test').Page, name: string) {
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

test('069 risk limits: preview, summary and two unsaved changes', async ({
  page,
}) => {
  await page.goto('/settings/risk');
  await expect(
    page.getByRole('heading', { name: 'Risk limits' }),
  ).toBeVisible({ timeout: 60_000 });
  // The debounced preview fills the "= $…" amounts and the summary box.
  await expect(
    page.getByText('What these limits mean today'),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/= \$/).first()).toBeVisible();

  // Two unsaved changes for the shot: risk per trade and max positions.
  const rpt = page.locator('#rpt');
  await rpt.fill((await rpt.inputValue()) === '0.5' ? '0.6' : '0.5');
  const npos = page.locator('#npos');
  await npos.fill((await npos.inputValue()) === '4' ? '5' : '4');
  await expect(page.getByText('2 unsaved changes')).toBeVisible();

  if (testInfo.project.name === 'desktop') {
    await shot(page, 'risk-limits-1440.png');
    await page.setViewportSize({ width: 1024, height: 900 });
    await shot(page, 'risk-limits-1024.png');
    await page.setViewportSize({ width: 1440, height: 900 });
  } else {
    await shot(page, 'risk-limits-390.png');
  }
});

test('069 trading: Advanced fold open', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await page.goto('/settings/trading');
  await expect(
    page.getByRole('heading', { name: 'Trading' }),
  ).toBeVisible({ timeout: 60_000 });
  const summary = page.locator('summary', {
    hasText: 'Advanced · prices and timeouts',
  });
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('all at default');
  await summary.click();
  await expect(page.locator('#qage')).toBeVisible();
  await shot(page, `trading-advanced-${suffix}.png`);
});

test('069 decision gates', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await page.goto('/settings/gates');
  await expect(
    page.getByRole('heading', { name: 'Decision gates' }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByText('Regimes that pass the trend gate'),
  ).toBeVisible();
  await expect(
    page.getByText('v6 sideways, high volatility: target at most'),
  ).toBeVisible();
  await shot(page, `decision-gates-${suffix}.png`);
});

test('069 looser-save confirmation', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await page.goto('/settings/risk');
  await expect(
    page.getByRole('heading', { name: 'Risk limits' }),
  ).toBeVisible({ timeout: 60_000 });
  const rpt = page.locator('#rpt');
  await rpt.fill((await rpt.inputValue()) === '0.5' ? '0.6' : '0.5');
  await page.getByLabel('Reason for this change').fill('069 screenshot');
  await page.getByRole('button', { name: /^Save as v/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Raises risk per trade/)).toBeVisible();
  await shot(page, `looser-save-${suffix}.png`);
  await dialog.getByRole('button', { name: 'Back' }).click();
});

test('069 phone: Find a setting above the Section select', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/settings/risk');
  await expect(
    page.getByRole('heading', { name: 'Risk limits' }),
  ).toBeVisible({ timeout: 60_000 });
  const find = page.getByLabel('Find a setting');
  const select = page.locator('select[aria-label="Section"]');
  await expect(find).toBeVisible();
  await expect(select).toBeVisible();
  // The select's optgroups carry the five group names.
  const groups = await select.locator('optgroup').evaluateAll((els) =>
    els.map((el) => el.getAttribute('label')),
  );
  expect(groups).toEqual([
    'Trading',
    'Model',
    'Alerts and display',
    'Connections',
    'Records',
  ]);
  await find.fill('risk per trade');
  await shot(page, 'phone-find-390.png');
});
