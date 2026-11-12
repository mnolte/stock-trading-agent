import { expect } from '@playwright/test';

import { test } from './shots';

// 056 step 3 — Settings shows one section per route; unsaved changes
// survive switching sections and Save writes them all.

test('settings: /settings/risk shows only Risk limits', async ({ page }) => {
  await page.goto('/settings/risk');
  await expect(
    page.getByRole('heading', { name: 'Risk limits' }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(
    page.locator('[data-settings-section]'),
  ).toHaveCount(1);
  await expect(
    page.getByRole('heading', { name: 'API keys and data' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Trading' }),
  ).toHaveCount(0);
});

test('settings: /settings reopens the last section used', async ({ page }) => {
  await page.goto('/settings/gates');
  await expect(
    page.getByRole('heading', { name: 'Decision gates' }),
  ).toBeVisible({ timeout: 60_000 });
  await page.goto('/settings');
  await expect(page).toHaveURL(/\/settings\/gates/);
  await expect(
    page.getByRole('heading', { name: 'Decision gates' }),
  ).toBeVisible();
});

test('settings: a Risk change survives switching to Trading and saves', async ({
  page,
}) => {
  page.on('dialog', (dialog) => void dialog.accept());
  await page.goto('/settings/risk');
  await expect(
    page.getByRole('heading', { name: 'Risk limits' }),
  ).toBeVisible({ timeout: 60_000 });

  const box = page.locator('#rpt');
  // Always raise — a cached e2e DB can already hold 0.6, and a lower
  // value is tighter and skips the looser-change dialog (item 069).
  await box.fill(String(Math.round((Number(await box.inputValue()) + 0.1) * 100) / 100));
  await expect(page.getByText('1 unsaved change')).toBeVisible();

  // Switching sections keeps the edit; the save bar lists it everywhere.
  // Desktop shows the section list; a phone shows the Section select.
  const sectionSelect = page.locator('select[aria-label="Section"]');
  if (await sectionSelect.count()) {
    await sectionSelect.selectOption('trading');
  } else {
    await page.getByRole('link', { name: 'Trading', exact: true }).click();
  }
  await expect(
    page.getByRole('heading', { name: 'Trading' }),
  ).toBeVisible();
  await expect(page.getByText('1 unsaved change')).toBeVisible();

  await page.getByLabel('Reason for this change').fill('e2e: risk tweak');
  await page.getByRole('button', { name: /^Save as v/ }).click();
  // 0.5 → 0.6% loosens the agent: the save bar asks first and names the
  // looser change (item 069).
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Raises risk per trade/)).toBeVisible();
  await dialog.getByRole('button', { name: /^Save as v/ }).click();
  await expect(
    page.getByText(/Saved as v\d+ · applies at the next cycle/),
  ).toBeVisible();
});
