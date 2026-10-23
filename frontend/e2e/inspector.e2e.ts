import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 4 screenshots are for docs/screenshots/pr-4/ (pass SCREENSHOT_DIR, see ./shots), matching the PR 3
// convention; design/screenshots/ holds the design references only.
const outDir = screenshotDir('pr-4');

test('LLM inspector and decision detail', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  const name = (base: string) => `${base}-${suffix}.png`;

  await page.goto('/inspector');

  const rows = page.locator('[aria-label="Model calls"] button');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByRole('heading', { name: 'Calls today' })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Raw response' })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, name('inspector')),
    fullPage: true,
  });

  // Open the stored decision behind the selected call. The drawer layout
  // (PR 9) shows the raw input snapshot in this inspector only.
  await page.getByRole('link', { name: 'Open decision' }).click();
  await expect(page).toHaveURL(/\/decisions\/\d+/);
  await expect(
    page.getByRole('heading', { name: 'Safety gates', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Input snapshot', exact: true })
  ).not.toBeVisible();

  // The raw input snapshot itself stays here, in the call details.
  await page.goBack();
  await expect(page.getByText('User · input snapshot')).toBeVisible();
});
