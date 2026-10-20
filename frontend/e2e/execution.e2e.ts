import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 5 screenshots are for docs/screenshots/pr-5/ (pass SCREENSHOT_DIR, see ./shots), matching earlier PRs;
// design/screenshots/ holds the design references only.
const outDir = screenshotDir('pr-5');

test('kill switch flow and positions page', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  const name = (base: string) => `${base}-${suffix}.png`;

  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Kill switch' })).toBeVisible({
    timeout: 60_000,
  });

  // Positions & orders page, while the session is still open.
  await page.goto('/positions');
  await expect(page.getByRole('heading', { name: /open positions/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /order history/i })).toBeVisible();
  await page.screenshot({ path: path.join(outDir, name('positions')), fullPage: true });
  await page.goto('/');

  // Kill switch choice dialog.
  await page.getByRole('button', { name: 'Kill switch' }).click();
  await expect(page.getByRole('dialog', { name: 'Kill switch' })).toBeVisible();
  await page.screenshot({ path: path.join(outDir, name('kill-switch')) });

  // Stop & flatten confirmation.
  await page.getByRole('radio').nth(1).check({ force: true });
  await page.getByRole('button', { name: 'Turn on kill switch' }).click();
  await expect(
    page.getByRole('dialog', { name: 'Sell everything now?' })
  ).toBeVisible();
  await page.screenshot({ path: path.join(outDir, name('flatten-confirm')) });
  await page.getByRole('button', { name: 'Cancel' }).click();

  // Activate the kill switch for real: the banner and halted header appear.
  await page.getByRole('button', { name: 'Kill switch' }).click();
  await page.getByRole('button', { name: 'Turn on kill switch' }).click();
  await expect(
    page.getByText('Kill switch is on', { exact: false })
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Trading halted' })).toBeVisible();
  await page.screenshot({ path: path.join(outDir, name('kill-switch-on')), fullPage: true });

  // Resume dialog — the "Trading halted" header button is unambiguous:
  // the pause feature has its own one-click Resume buttons elsewhere.
  await page.getByRole('button', { name: 'Trading halted' }).click();
  const resumeDialog = page.getByRole('dialog', { name: 'Resume trading?' });
  await expect(resumeDialog).toBeVisible();
  await page.screenshot({ path: path.join(outDir, name('resume')) });
  await resumeDialog.getByRole('button', { name: 'Cancel' }).click();

  // Resume for real so the test leaves the app in a clean state.
  await page.getByRole('button', { name: 'Trading halted' }).click();
  await page.getByPlaceholder(/checked the reconciliation/).fill('e2e done');
  await page
    .getByRole('dialog', { name: 'Resume trading?' })
    .getByRole('button', { name: 'Resume' })
    .click();
  await expect(page.getByRole('button', { name: 'Kill switch' })).toBeVisible({
    timeout: 15_000,
  });
});

test('states gallery', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  const name = (base: string) => `${base}-${suffix}.png`;

  await page.goto('/states');
  await expect(page.getByRole('heading', { name: /banners/i })).toBeVisible({
    timeout: 60_000,
  });
  await page.screenshot({ path: path.join(outDir, name('states')), fullPage: true });
});
