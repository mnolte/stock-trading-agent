import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

const shots = screenshotDir('pr-25');

// PR 25: in-app error pages — route and record 404s, the per-screen
// error boundary, the first-load "isn't answering" page and the offline
// banner (docs/SPEC.md → Error pages and API errors).

test('an unknown route shows the 404 page', async ({ page }) => {
  await page.goto('/positons');
  await expect(
    page.getByRole('heading', { name: 'There is no page here' }),
  ).toBeVisible();
  await expect(page.getByText('404 · not found')).toBeVisible();
  await page.getByRole('link', { name: 'Go to Dashboard' }).click();
  await expect(page).toHaveURL(/\/$/);
});

test('a missing decision id shows the record not-found page', async ({
  page,
}) => {
  await page.goto('/decisions/99999999');
  await expect(
    page.getByRole('heading', { name: "Decision 99999999 doesn't exist" }),
  ).toBeVisible();
  await expect(
    page.getByText('It may be from a run that was reset'),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Search the LLM inspector' }),
  ).toBeVisible();
  await page.screenshot({ path: `${shots}/record-not-found.png` });
});

test('a render error on one screen leaves the shell working', async ({
  page,
}) => {
  await page.goto('/dev/components?crash');
  await expect(
    page.getByRole('heading', { name: 'Something went wrong on this page' }),
  ).toBeVisible();
  // The shell stays — sidebar nav on desktop, the menu button on mobile.
  await expect(
    page
      .getByRole('link', { name: 'Positions' })
      .or(page.getByRole('button', { name: 'Menu' })),
  ).toBeVisible();
  // The report may or may not land in simulated mode; either way the
  // Copy button exists (error id when the report returned one).
  await expect(
    page.getByRole('button', { name: /Copy (error id|details)/ }),
  ).toBeVisible();
  await page.screenshot({ path: `${shots}/screen-boundary.png` });
});

test('first load with the API down shows the retry page', async ({
  page,
}) => {
  // Match only API calls — /src/api/*.ts modules must still load.
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => route.abort(),
  );
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: "The app isn't answering" }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Trying again every 15 seconds')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Try now' }),
  ).toBeVisible();
  await page.screenshot({ path: `${shots}/not-answering.png` });
});

test('going offline shows the banner', async ({ page, context }) => {
  await page.goto('/');
  await expect(page.getByText('Dashboard').first()).toBeVisible();
  await context.setOffline(true);
  await expect(
    page.getByText('offline', { exact: false }),
  ).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${shots}/offline.png` });
  await context.setOffline(false);
});
