import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// PR screenshots land in docs/screenshots/pr-<n>/ (design/ stays untouched),
// named states-1440/-390 next to the design reference States.png.
const outDir =
  process.env.SCREENSHOT_DIR ??
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../docs/screenshots/pr-8'
  );

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
