import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 6 screenshots are for docs/screenshots/pr-6/ (pass SCREENSHOT_DIR, see ./shots), matching earlier PRs;
// design/screenshots/ holds the design references only.
const outDir = screenshotDir('pr-6');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('dashboard: cycle line, stat cards, chart, feed', async ({ page }, testInfo) => {
  await page.goto('/');

  await expect(page.getByRole('button', { name: 'Kill switch' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Open risk')).toBeVisible();
  await expect(page.getByText('Re-entry')).toBeVisible();
  await expect(page.getByText('Since start')).toBeVisible();
  await expect(page.locator('[aria-label="Watchlist"] button.sa-row').first()).toBeVisible();
  await expect(page.getByText("Today's decisions")).toBeVisible();
  await expect(page.locator('[data-testid="price-chart"]')).toBeVisible();

  await page.screenshot({ path: shot('main', testInfo.project.name), fullPage: true });
});

test('positions: recon strip, cards, account, order history', async ({ page }, testInfo) => {
  await page.goto('/positions');

  await expect(page.getByText(/In sync with Alpaca|Reconciliation mismatch/)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/Open positions ·/)).toBeVisible();
  await expect(page.getByText('Not to be bought again today')).toBeVisible();
  await expect(page.getByText('Account at Alpaca')).toBeVisible();
  await expect(page.getByText('Order history')).toBeVisible();

  await page.screenshot({ path: shot('positions', testInfo.project.name), fullPage: true });
});

test('decision detail: sections render', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByText("Today's decisions")).toBeVisible({ timeout: 60_000 });

  const items = await page.evaluate(async () => {
    const res = await fetch('/api/decisions');
    const body = await res.json();
    return body.items as { id: number }[];
  });
  test.skip(items.length === 0, 'no decisions recorded in this run');

  await page.goto(`/decisions/${items[0].id}`);
  await expect(page.getByRole('heading', { name: 'Safety gates' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Signal stack' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Agent reasoning' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Outcome', exact: true })).toBeVisible();

  await page.screenshot({ path: shot('decision', testInfo.project.name), fullPage: true });
});

test('settings: sections, unsaved bar, reset dialog', async ({ page }, testInfo) => {
  await page.goto('/settings');

  await expect(page.getByRole('heading', { name: 'API keys and data' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByLabel('Universe')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Risk limits' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Settings history' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Paper account', exact: true })).toBeVisible();

  // The unsaved-changes bar opens on the first edit; Save stays off
  // until a reason is typed (PR 10: reason is required, no dialog).
  const saveButton = page.getByRole('button', { name: /Save as v/ });
  await expect(saveButton).toHaveCount(0);
  await page.locator('#brief').click();
  await expect(page.getByText('unsaved change')).toBeVisible();
  await expect(saveButton).toBeDisabled();
  await page.getByLabel('Reason for this change').fill('e2e check');
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  await expect(page.getByText(/Saved as v\d+/)).toBeVisible();
  await page.screenshot({ path: shot('settings-save', testInfo.project.name) });

  // Reset requires typing RESET before the confirm button enables.
  await page.getByRole('button', { name: 'Reset paper account' }).click();
  const resetDialog = page.getByRole('dialog', { name: 'Reset the paper account?' });
  await expect(resetDialog).toBeVisible();
  await expect(resetDialog.getByRole('button', { name: 'Reset account' })).toBeDisabled();
  await resetDialog.getByLabel('Type RESET to confirm').fill('RESET');
  await expect(resetDialog.getByRole('button', { name: 'Reset account' })).toBeEnabled();
  await resetDialog.getByRole('button', { name: 'Cancel' }).click();

  await page.screenshot({ path: shot('settings', testInfo.project.name), fullPage: true });
});

test('states gallery: banners, approval, dialogs', async ({ page }, testInfo) => {
  await page.goto('/states');

  await expect(page.getByRole('heading', { name: 'Session states' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByRole('heading', { name: 'Banners' })).toBeVisible();
  await expect(page.getByText('Pending approval · supervised mode').first()).toBeVisible();

  await page.getByRole('button', { name: 'Reset paper account' }).click();
  await expect(page.getByRole('dialog', { name: 'Reset the paper account?' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();

  await page.screenshot({ path: shot('states', testInfo.project.name), fullPage: true });
});

test('phone width: hamburger opens the nav', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'mobile-only check');
  await page.goto('/');

  const menu = page.getByRole('button', { name: 'Menu' });
  await expect(menu).toBeVisible({ timeout: 60_000 });
  await menu.click();
  await expect(page.getByRole('dialog', { name: 'Menu' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Menu' }).getByText('Positions & orders')).toBeVisible();
});
