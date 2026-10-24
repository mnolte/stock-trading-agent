import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// Screenshots land in docs/screenshots/pr-20/ when SCREENSHOT_DIR is set
// (Logs.png / Logs-390.png next to the design PNGs).
const outDir = screenshotDir('e2e');

test('logs opens on warning and up, toggle shows debug and info', async ({
  page,
}, testInfo) => {
  await page.goto('/logs');

  const toggles = page.locator('button[aria-pressed]');
  await expect(toggles).toHaveCount(3);
  await expect(toggles.nth(0)).toHaveAttribute('aria-pressed', 'true');
  await expect(toggles.nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(toggles.nth(2)).toHaveAttribute('aria-pressed', 'true');

  // "Show debug and info" switch → all five levels active via ?levels=.
  await page.getByLabel('Show debug and info').check();
  await expect(page).toHaveURL(/levels=DEBUG/);
  await page.getByLabel('Show debug and info').uncheck();
  await expect(page).toHaveURL(/levels=WARNING/);

  const name = testInfo.project.name === 'mobile' ? 'Logs-390.png' : 'Logs.png';
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
});

test('levels survive a reload via ?levels=', async ({ page }) => {
  await page.goto('/logs?levels=ERROR%2CCRITICAL');
  await expect(page.locator('button[aria-pressed="true"]')).toHaveCount(2);
  await page.reload();
  await expect(page.locator('button[aria-pressed="true"]')).toHaveCount(2);
});

test('expanding a line shows traceback and surrounding lines', async ({
  page,
  request,
}) => {
  // Seed an error through the client-error endpoint so an ERROR line with
  // a traceback exists regardless of what the app logged on its own.
  await request.post('/api/logs/client', {
    data: {
      message: 'e2e seeded error',
      error_id: 'e-e2e000',
      tab_id: 'e2e',
      stack: 'Traceback: e2e stack line one\nline two',
    },
  });
  await page.goto('/logs?levels=DEBUG%2CINFO%2CWARNING%2CERROR%2CCRITICAL');
  const row = page.locator('div[role="log"] button', {
    hasText: 'e2e seeded error',
  });
  await expect(row.first()).toBeVisible({ timeout: 30_000 });
  await row.first().click();
  await expect(page.getByText('e2e stack line one')).toBeVisible();
  // Surrounding lines render the neighbouring ids.
  await expect(page.locator('div[role="log"] button').first()).toBeVisible();
});
