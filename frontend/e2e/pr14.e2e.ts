import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 14 screenshots are for docs/screenshots/pr-14/ (pass SCREENSHOT_DIR, see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-14');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('backtest: form with estimate and cutoff refusal, runs list', async ({
  page,
  request,
}, testInfo) => {
  // The e2e database is shared between runs: remove leftover run rows first.
  const before = await (await request.get('/api/backtests')).json();
  for (const row of before.runs) {
    await request.delete(`/api/backtests/${row.id}`);
  }

  await page.goto('/backtest');
  await expect(page.getByText('New backtest', { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Runs', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Strategies to compare')).toBeVisible();

  // A range after every configured model's cutoff: the estimate fills in
  // sessions, bars, calls, cache hits and cost.
  await page.getByLabel('From').fill('2024-07-01');
  await page.getByLabel('To').fill('2024-09-30');
  await expect(page.getByText('Model calls (estimate)')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Sessions', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Run backtest' }),
  ).toBeEnabled();
  await page.screenshot({ path: shot('backtest-form', testInfo.project.name) });

  // A start before the live model's knowledge cutoff disables the run and
  // says why, both in the estimate and on the button.
  await page.getByLabel('From').fill('2023-01-02');
  await expect(
    page.getByText(/knowledge cutoff|cutoff/i).first(),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole('button', { name: 'Run backtest' }),
  ).toBeDisabled();
  await page.screenshot({ path: shot('backtest-cutoff', testInfo.project.name) });

  // The server refuses the run too — the row lands in the list with its reason.
  const created = await request.post('/api/backtests', {
    data: {
      name: 'Too early',
      start: '2023-01-02',
      end: '2023-06-30',
      universe_mode: 'fixed',
      strategies: ['live'],
      starting_equity: 10000,
      slippage_pct: 0.05,
    },
  });
  expect(created.ok()).toBeTruthy();
  const body = await created.json();
  expect(body.status).toBe('refused');

  await page.reload();
  await expect(
    page.getByRole('button', { name: /Too early/ }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/refused/i).first()).toBeVisible();
  await page.screenshot({ path: shot('backtest-runs', testInfo.project.name) });
});
