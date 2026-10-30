import path from 'node:path';

import { expect, test } from '@playwright/test';

// PR 29 screenshots → docs/screenshots/pr-29/ (next to the design PNGs
// for Settings, Inspector and Logs). Simulated mode: the seeded app has
// no keys saved, so every model row shows as unset and the model pickers
// list "id · provider".
const outDir = path.resolve('../docs/screenshots/pr-29');

async function shot(page: import('@playwright/test').Page, name: string, fullPage = true) {
  await page.screenshot({ path: path.join(outDir, name), fullPage });
}

test('settings — api keys + model pickers', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'API keys and data' })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Anthropic (optional)')).toBeVisible({ timeout: 60_000 });
  await shot(page, 'settings.png');
});

test('inspector — call detail', async ({ page, request }) => {
  const list = await (await request.get('/api/llm-calls?limit=1')).json();
  test.skip(!list.lines?.length, 'no llm calls in the seeded database');
  const id = list.lines[0].id;
  await page.goto(`/inspector?call=${id}`);
  await expect(page.getByText('Tokens in / out')).toBeVisible({ timeout: 60_000 });
  await shot(page, 'inspector.png');
});

test('logs — component filter', async ({ page }) => {
  await page.goto('/logs');
  const component = page.getByRole('searchbox', { name: 'Component' });
  await expect(component).toBeVisible({ timeout: 60_000 });
  await component.fill('model.');
  await shot(page, 'logs.png');
});
