import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// PR 6 screenshots land in docs/screenshots/pr-6/, matching earlier PRs;
// design/screenshots/ holds the design references only.
const outDir =
  process.env.SCREENSHOT_DIR ??
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../docs/screenshots/pr-6'
  );

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('dashboard: cycle line, stat cards, chart, feed', async ({ page }, testInfo) => {
  await page.goto('/');

  await expect(page.getByRole('button', { name: 'Kill switch' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Open risk')).toBeVisible();
  await expect(page.getByText('Day trades')).toBeVisible();
  await expect(page.getByText('Since start')).toBeVisible();
  await expect(page.locator('[aria-label="Watchlist"] button[role="row"]').first()).toBeVisible();
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
  await expect(page.getByText('Day trades')).toBeVisible();
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
  await expect(page.getByRole('heading', { name: 'Code checks' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Risk gates' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Outcome', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Input snapshot' })).toBeVisible();

  await page.screenshot({ path: shot('decision', testInfo.project.name), fullPage: true });
});

test('settings: sections, save dialog, reset dialog', async ({ page }, testInfo) => {
  await page.goto('/settings');

  await expect(page.getByText('Risk limits')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Universe')).toBeVisible();
  await expect(page.getByText('Connections')).toBeVisible();
  await expect(page.getByText('Settings history')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Paper account', exact: true })).toBeVisible();

  // Save is disabled until something changes; editing enables it and the
  // confirm dialog shows the diff.
  const saveButton = page.getByRole('button', { name: /Save as v/ });
  await expect(saveButton).toBeDisabled();
  const riskField = page.getByRole('spinbutton', { name: /^Risk per trade/ });
  await riskField.fill('0.4');
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  const saveDialog = page.getByRole('dialog', { name: /Save settings as/ });
  await expect(saveDialog).toBeVisible();
  await expect(saveDialog).toContainText('Risk per trade');
  await page.screenshot({ path: shot('settings-save', testInfo.project.name) });
  await saveDialog.getByRole('button', { name: 'Cancel' }).click();

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
