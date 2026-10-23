import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR screenshots are for docs/screenshots/pr-10/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched):
// the rebuilt Settings screen — section list, API-key rows, the
// unsaved-changes bar and inline validation.
const outDir = screenshotDir('pr-10');

test('settings: sections, key rows and the unsaved-changes bar', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/settings');

  // Section list + the API keys block: three connection rows, masked
  // hints only — never a key. In simulated mode the rows show "missing".
  await expect(
    page.getByRole('heading', { name: 'API keys and data' })
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Alpaca paper keys')).toBeVisible();
  await expect(page.getByText('OpenAI', { exact: true })).toBeVisible();
  await expect(page.getByText('Finnhub', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Keys are stored encrypted on this computer')
  ).toBeVisible();

  // The other sections from the design are all reachable in the list.
  for (const name of [
    'Trading',
    'Risk limits',
    'Entry rules and checks',
    'Earnings',
    'Model and reviews',
    'Schedule',
    'Settings history',
    'Paper account',
  ]) {
    await expect(
      page.getByRole('heading', { name, exact: true })
    ).toBeVisible();
  }

  // An edit marks the row changed, shows Reset and opens the
  // unsaved-changes bar, which demands a reason before Save.
  await page.locator('#brief').click();
  await expect(page.getByText('Changed from').first()).toBeVisible();
  await expect(page.getByText('1 unsaved change')).toBeVisible();
  await expect(
    page.getByRole('button', { name: /^Save as v/ })
  ).toBeDisabled();

  await page.screenshot({
    path: path.join(outDir, `settings-${suffix}.png`),
    fullPage: true,
  });
});

test('settings: RSI from ≥ to is invalid and blocks Save', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'API keys and data' })
  ).toBeVisible({ timeout: 60_000 });

  // The RSI row's "from" box is the first in the Entry rules section:
  // set it to 80, above the 70 upper bound — Save stays off.
  await page
    .locator('section#rules')
    .getByLabel('from', { exact: true })
    .first()
    .fill('80');
  await expect(
    page.getByText('RSI from must be below RSI to').first()
  ).toBeVisible();
  await page.locator('#brief').click(); // make the form dirty
  await expect(
    page.getByRole('button', { name: /^Save as v/ })
  ).toBeDisabled();

  await page.screenshot({
    path: path.join(outDir, `settings-invalid-${suffix}.png`),
    fullPage: true,
  });
});
