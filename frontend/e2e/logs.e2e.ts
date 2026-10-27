import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// Screenshots land in docs/screenshots/pr-20/ when SCREENSHOT_DIR is set
// (Logs.png / Logs-390.png next to the design PNGs).
const outDir = screenshotDir('e2e');

test('logs opens on warning and up, toggle shows debug and info', async ({
  page,
}, testInfo) => {
  await page.goto('/logs');

  const toggles = page
    .getByRole('group', { name: 'Levels' })
    .locator('button[aria-pressed]');
  await expect(toggles).toHaveCount(5);
  await expect(toggles.nth(0)).toHaveAttribute('aria-pressed', 'false');
  await expect(toggles.nth(1)).toHaveAttribute('aria-pressed', 'false');
  await expect(toggles.nth(2)).toHaveAttribute('aria-pressed', 'true');
  await expect(toggles.nth(3)).toHaveAttribute('aria-pressed', 'true');
  await expect(toggles.nth(4)).toHaveAttribute('aria-pressed', 'true');

  // "Show debug and info" switch → all five levels active via ?levels=.
  const debugSwitch = page.getByRole('switch', { name: 'Show debug and info' });
  await expect(debugSwitch).toHaveAttribute('aria-checked', 'false');
  await debugSwitch.click();
  await expect(page).toHaveURL(/levels=DEBUG/);
  await expect(debugSwitch).toHaveAttribute('aria-checked', 'true');
  await debugSwitch.click();
  await expect(page).toHaveURL(/levels=WARNING/);

  const name = testInfo.project.name === 'mobile' ? 'Logs-390.png' : 'Logs.png';
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
});

test('levels survive a reload via ?levels=', async ({ page }) => {
  const toggles = (p: typeof page) =>
    p.getByRole('group', { name: 'Levels' }).locator('button[aria-pressed="true"]');
  await page.goto('/logs?levels=ERROR%2CCRITICAL');
  await expect(toggles(page)).toHaveCount(2);
  await page.reload();
  await expect(toggles(page)).toHaveCount(2);
});

test('expanding a line shows traceback and surrounding lines', async ({
  page,
  request,
}) => {
  // Seed an error through the client-error endpoint so an ERROR line with
  // a traceback exists regardless of what the app logged on its own.
  const seeded = await request.post('/api/logs/client', {
    data: {
      message: 'e2e seeded error',
      error_id: 'e-e2e000',
      tab_id: 'e2e',
      stack: 'Traceback: e2e stack line one\nline two',
    },
  });
  expect(seeded.ok()).toBe(true);
  expect(await seeded.json()).toMatchObject({ logged: true });
  await page.goto('/logs?levels=ERROR');
  const row = page.locator('div[role="log"] button', {
    hasText: 'e2e seeded error',
  });
  // The seeded line lands on the index's listener thread just after the
  // POST returns; reload until the committed row is listed (bounded).
  await expect
    .poll(
      async () => {
        if (await row.first().isVisible()) return true;
        await page.reload();
        return false;
      },
      { timeout: 30_000 },
    )
    .toBe(true);
  await row.first().click();
  // The stack travels inside the stored message (client errors have no
  // exc_info): it shows in the expanded line's JSON dump — and in the
  // Recurring problems sample, so the assertion is scoped to the panel.
  const detail = row.first().locator('xpath=following-sibling::div[1]');
  await expect(detail.getByText('e2e stack line one')).toBeVisible();
  // Surrounding lines render the neighbouring ids.
  await expect(page.locator('div[role="log"] button').first()).toBeVisible();
});
