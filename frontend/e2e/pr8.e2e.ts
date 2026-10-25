import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR screenshots are for docs/screenshots/pr-<n>/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched),
// named states-1440/-390 next to the design reference States.png.
const outDir = screenshotDir('pr-8');

const shot = (project: string) =>
  path.join(outDir, `states-${project === 'mobile' ? '390' : '1440'}.png`);

test('states: error banners and dialogs gallery', async ({ page }, testInfo) => {
  await page.goto('/states');

  await expect(
    page.getByRole('heading', { name: 'States & dialogs', level: 2 })
  ).toBeVisible({ timeout: 60_000 });

  // Error states from the PR 8 spec section (Risk management, Errors):
  // kill switch (manual and automatic), position without a stop,
  // reconciliation mismatch, data source down, market data missing.
  for (const label of [
    'Kill switch on · manual',
    'Kill switch on · automatic',
    'Position without a stop',
    'Reconciliation mismatch',
    'Data source down',
    'Market data missing',
    'Shock brake',
    'Daily loss limit',
    'Day-trade guard',
    'Losing streak',
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }

  // Dialogs section.
  const dialogs = page.getByRole('button', {
    name: 'Sell everything confirmation',
  });
  await expect(dialogs).toBeVisible();

  await page.screenshot({ path: shot(testInfo.project.name), fullPage: true });
});
