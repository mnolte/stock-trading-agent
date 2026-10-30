import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 13 screenshots are for docs/screenshots/pr-13/ (pass SCREENSHOT_DIR, see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-13');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('strategies: New shadow dialog, compare table, promote disabled under 20 trades', async ({
  page,
  request,
}, testInfo) => {
  // The e2e database is shared between runs: free every slot first.
  const before = await (await request.get('/api/strategies')).json();
  for (const row of before.rows) {
    if (row.id !== null) await request.post(`/api/strategies/${row.id}/archive`);
  }
  await request.post('/api/strategies', {
    data: {
      name: 'A · Stricter trend',
      kind: 'llm',
      prompt_version: 'decision.v1',
      model: 'gpt-4o-mini',
      temperature: 0,
      overrides: { rsi_min: 45, rsi_max: 65 },
    },
  });
  await request.post('/api/strategies', {
    data: { name: 'B · Bigger model', kind: 'llm', model: 'gpt-4.1-mini' },
  });

  await page.goto('/strategies');
  await expect(page.getByRole('heading', { name: 'Compare', exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId('shadow-cost')).toContainText('2 of 3 slots');
  const current = await (await request.get('/api/strategies')).json();
  await expect(page.getByTestId('synthetic-quote-count')).toContainText(
    `${current.synthetic_quote_entries} synthetic-quote entries this run`,
  );

  // New shadow dialog: name, kind, prompt, model, temperature, overrides.
  await page.getByRole('button', { name: 'New shadow…' }).click();
  const dialog = page.getByRole('dialog', { name: 'New shadow' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('Model')).toBeVisible();
  // Every priced model in the `models` block is offered (docs/SPEC.md →
  // Model providers — OpenAI and Anthropic entries both count).
  await expect(dialog.getByLabel('Model').locator('option')).toHaveText(
    ['claude-haiku-5-5 · Anthropic', 'claude-sonnet-5-5 · Anthropic', 'gpt-4.1-mini · OpenAI', 'gpt-4o-mini · OpenAI'],
  );
  await expect(dialog.getByLabel('Min confidence to enter')).toBeVisible();
  await page.screenshot({ path: shot('strategies-new-shadow', testInfo.project.name) });

  await dialog.getByLabel('Name').fill('Rules only');
  await dialog.getByRole('button', { name: 'Rules only' }).click();
  // Code checks only apply to model answers.
  await expect(dialog.getByLabel('Min confidence to enter')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Create shadow' }).click();
  await expect(dialog).toHaveCount(0);

  // Live first, then the shadows and the baseline.
  const table = page.locator('table');
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await expect(table.locator('tbody tr').first()).toContainText('Live');
  await expect(table.getByText('baseline', { exact: true })).toBeVisible();
  await expect(page.getByTestId('shadow-cost')).toContainText('3 of 3 slots');
  await expect(page.getByRole('button', { name: 'New shadow…' })).toBeDisabled();
  await expect(page.getByText('far too few to tell strategies apart')).toBeVisible();

  // Select B: what differs from live, and Promote disabled under 20 trades.
  await table.locator('tbody tr', { hasText: 'B · Bigger model' }).click();
  const card = page.locator('section', { has: page.getByRole('heading', { name: 'B · Bigger model' }) });
  await expect(card).toContainText('model gpt-4o-mini → gpt-4.1-mini');
  const promote = card.getByRole('button', { name: 'Promote to live…' });
  await expect(promote).toBeDisabled();
  await expect(promote).toHaveAttribute('title', 'Needs at least 20 closed trades (has 0)');
  await expect(page.getByRole('heading', { name: 'Where B and live disagreed' })).toBeVisible();

  await page.screenshot({ path: shot('strategies', testInfo.project.name), fullPage: true });
});
