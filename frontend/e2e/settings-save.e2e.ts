import { expect } from '@playwright/test';

import { test } from './shots';

// Repository scan of 31 October: PR 29 broke two settings saves —
// reviews.models was overwritten by the quiet-hours cleaner and
// notifications.quiet_hours had no validation branch left. These specs
// save both through the real page.

test('settings: a review model change saves', async ({ page }) => {
  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'Model and reviews' }),
  ).toBeVisible({ timeout: 60_000 });

  // Pick any enabled option that is not the current weekly model.
  const select = page.locator('#rmodel-weekly');
  const current = await select.inputValue();
  const other = await select
    .locator('option:not([disabled])')
    .evaluateAll(
      (els, cur) =>
        els
          .map((el) => (el as HTMLOptionElement).value)
          .find((v) => v !== cur),
      current,
    );
  expect(other).toBeTruthy();
  await select.selectOption(other as string);
  await expect(page.getByText('1 unsaved change')).toBeVisible();

  await page.getByLabel('Reason for this change').fill('e2e: weekly model');
  await page.getByRole('button', { name: /^Save as v/ }).click();
  await expect(
    page.getByText(/Saved as v\d+ · applies at the next cycle/),
  ).toBeVisible();
});

test('settings: quiet hours save', async ({ page }) => {
  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'Model and reviews' }),
  ).toBeVisible({ timeout: 60_000 });

  const notify = page.locator('section#notify');
  await notify.getByLabel('from', { exact: true }).fill('22:45');
  await notify.getByLabel('to', { exact: true }).fill('06:30');
  await expect(page.getByText('2 unsaved changes')).toBeVisible();

  await page.getByLabel('Reason for this change').fill('e2e: quiet hours');
  await page.getByRole('button', { name: /^Save as v/ }).click();
  await expect(
    page.getByText(/Saved as v\d+ · applies at the next cycle/),
  ).toBeVisible();
});

test('settings: a bad quiet-hours time is invalid and blocks Save', async ({
  page,
}) => {
  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { name: 'Model and reviews' }),
  ).toBeVisible({ timeout: 60_000 });

  const notify = page.locator('section#notify');
  await notify.getByLabel('from', { exact: true }).fill('99:99');
  await expect(
    page.getByText('Quiet hours from: use HH:MM, e.g. 23:00'),
  ).toBeVisible();

  await page.getByLabel('Reason for this change').fill('e2e: bad time');
  await expect(
    page.getByRole('button', { name: /^Save as v/ }),
  ).toBeDisabled();
});
