import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// PR 4 screenshots land in design/screenshots/ next to the matching design
// reference (Inspector.png, Decision.png). Suffixes keep the viewports apart.
const outDir =
  process.env.SCREENSHOT_DIR ??
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../design/screenshots'
  );

test('LLM inspector and decision detail', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

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
    path: path.join(outDir, `Inspector-${suffix}.png`),
    fullPage: true,
  });

  // Open the stored decision behind the selected call.
  await page.getByRole('link', { name: 'Open decision' }).click();
  await expect(page).toHaveURL(/\/decisions\/\d+/);
  await expect(
    page.getByRole('heading', { name: 'Model output', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Code checks', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Rules pre-filter', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Input snapshot', exact: true })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `Decision-${suffix}.png`),
    fullPage: true,
  });
});
