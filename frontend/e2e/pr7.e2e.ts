import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR screenshots are for docs/screenshots/pr-<n>/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched),
// named performance-1440/-390 next to the design reference Performance.png.
const outDir = screenshotDir('pr-7');

const shot = (project: string) =>
  path.join(outDir, `performance-${project === 'mobile' ? '390' : '1440'}.png`);

test('performance: stat cards, equity chart, aggregates, closed trades', async ({ page }, testInfo) => {
  await page.goto('/performance');

  await expect(
    page.getByRole('heading', { name: 'Performance', level: 1 })
  ).toBeVisible({ timeout: 60_000 });

  // Six stat cards.
  await expect(page.getByText('Return', { exact: true })).toBeVisible();
  await expect(page.getByText('vs SPY hold')).toBeVisible();
  await expect(page.getByText('Adjusted P&L')).toBeVisible();
  await expect(page.getByText('Win rate')).toBeVisible();
  await expect(page.getByText('Average R')).toBeVisible();
  await expect(page.getByText('Max drawdown')).toBeVisible();

  // Equity chart with the USD/EUR toggle.
  await expect(page.getByText('Equity vs SPY buy-and-hold')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Currency' })).toBeVisible();
  await expect(page.locator('[data-testid="equity-chart"]')).toBeVisible();

  // Aggregates and tables.
  await expect(page.getByRole('heading', { name: 'By regime' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'By symbol' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Decisions and outcomes' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Closed trades' })).toBeVisible();

  await page.screenshot({ path: shot(testInfo.project.name), fullPage: true });
});
