import { readFileSync } from 'node:fs';
import { expect, type Page } from '@playwright/test';

import { test } from './shots';

const controls: Record<string, string> = JSON.parse(
  readFileSync(new URL('../public/settings-controls.json', import.meta.url), 'utf8'),
);

const hidden = {
  key: 'concurrency.max_requests', label: 'Requests at once', saved: 10, config: 20,
  since_version: 12, since_at: '2026-10-21T12:00:00Z', direction: 'looser',
};

async function mockSettings(page: Page, items = [hidden], noEntries = true) {
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const response = await route.fetch();
    const body = await response.json();
    body.saved_without_control = items;
    body.settings.schedule.last_cycle_no_entries = noEntries;
    await route.fulfill({ json: body });
  });
}

test('last-cycle switch previews unsaved entries and marks the loosening', async ({ page }) => {
  await mockSettings(page);
  await page.goto('/settings/schedule');
  const toggle = page.getByRole('switch', { name: 'New entries in the last cycle' });
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('#schedule tbody tr').last().locator('td').last()).toHaveText('no');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('#schedule tbody tr').last()).toContainText('yes, sent by 15:50');
  const review = page.getByRole('button', { name: /Review and save/ });
  if (await review.isVisible()) await review.click();
  await expect(page.getByRole('region', { name: 'Unsaved changes' })).toContainText('New entries in the last cycle off → on');
  await expect(page.getByText('looser', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Discard', exact: true }).click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await page.getByRole('searchbox', { name: 'Find a setting' }).fill('New entries in the last cycle');
  const found = page.getByRole('checkbox', { name: 'New entries in the last cycle' });
  await expect(found).not.toBeChecked();
  await found.check();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('#schedule tbody tr').last()).toContainText('yes, sent by 15:50');
});

test('History shows hidden saved values, prefills the reason and clears after recovery', async ({ page }) => {
  let items = [hidden];
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), saved_without_control: items } });
  });
  await page.route('**/api/settings/use-config', async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      key: hidden.key, reason: `Take over the config value of ${hidden.key}`,
    });
    items = [];
    await route.fulfill({ json: { version: 2, effective_from: 'restart required', changes: [] } });
  });
  await page.goto('/settings/history');
  const block = page.getByRole('region', { name: 'Saved values without a control' });
  await expect(block).toContainText(hidden.key);
  await expect(block).toContainText('v12');
  const sectionNav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(sectionNav).toContainText('History · 1');
  await block.getByRole('button', { name: 'Use the config value' }).click();
  const dialog = page.getByRole('dialog', { name: 'Use the config value' });
  await expect(dialog.getByRole('textbox')).toHaveValue(`Take over the config value of ${hidden.key}`);
  await dialog.getByRole('button', { name: 'Use the config value' }).click();
  await expect(block).toHaveCount(0);
});

test('every shared control key has an actual form control on its section', async ({ page }) => {
  await page.route('**/api/auth/status', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), required: true, signed_in: true } });
  });
  const routes: Record<string, string> = { 'entry-selection': 'selection', notify: 'notifications', ready: 'readiness' };
  const rendered = new Set<string>();
  for (const section of new Set(Object.values(controls))) {
    await page.goto(`/settings/${routes[section] ?? section}`);
    await expect(page.locator('[data-settings-section]')).toBeVisible();
    for (const [key, expectedSection] of Object.entries(controls)) {
      if (expectedSection !== section) continue;
      const row = page.locator(`[data-setting~="${key}"]`).first();
      const inputs = row.locator('input,select,textarea,button[role="switch"],button[role="radio"],button[role="checkbox"],button[aria-pressed]');
      await expect(inputs.first()).toBeAttached();
      const keys = (await row.getAttribute('data-setting') ?? '').split(' ');
      expect(await inputs.count()).toBeGreaterThanOrEqual(keys.length);
      rendered.add(key);
    }
  }
  expect(Object.keys(controls).filter((key) => !rendered.has(key))).toEqual([]);
});
