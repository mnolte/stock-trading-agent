import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 14 screenshots are for docs/screenshots/pr-14/ (pass SCREENSHOT_DIR, see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-14');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('backtest: results panels from the seeded finished run', async ({
  page,
  request,
}, testInfo) => {
  // tests/e2e_backtest_seed.py leaves a `done` run (2026-09-14 – 2026-09-25,
  // live + rules-only, 10 sessions) in the shared e2e database.
  await page.goto('/backtest');
  const runButton = page.getByRole('button', { name: /2026-09-14/ });
  await expect(runButton).toBeVisible({ timeout: 60_000 });
  await expect(runButton).toContainText('done');
  await runButton.click();

  // Metric row.
  await expect(
    page.getByText('Closed trades', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('vs SPY buy and hold, pts')).toBeVisible();
  await expect(
    page.getByText('Average R', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Time in market')).toBeVisible();

  // Equity chart + legend (Lightweight Charts draws on a canvas).
  await expect(page.getByText('% of starting equity')).toBeVisible();
  await expect(
    page.locator('canvas').first(),
  ).toBeVisible();
  await expect(page.getByText('SPY buy and hold').first()).toBeVisible();

  // Regime table — the seed gives every entry an up · normal regime.
  await expect(page.getByText('By market regime')).toBeVisible();
  await expect(page.getByText('up · normal').first()).toBeVisible();

  // Read-with-care notes.
  await expect(page.getByText('Read with care')).toBeVisible();
  await expect(
    page.getByText(/came from the cache of earlier runs/),
  ).toBeVisible();

  await page.screenshot({ path: shot('backtest-results', testInfo.project.name) });

  // Trades panel — both strategies, real rows.
  await page.getByRole('button', { name: /Open trades/ }).click();
  await expect(page.getByText('Trades · Live settings')).toBeVisible();
  await expect(page.getByText('Trades · Rules only')).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'AAA' }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'target' }).first(),
  ).toBeVisible();

  // Decisions panel — skip row and a cache source tag.
  await page.getByRole('button', { name: 'Open decisions' }).click();
  await expect(page.getByText('Decisions · Live settings')).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'DDD' }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'cache' }).first(),
  ).toBeVisible();

  // A decision row opens the same drawer live decisions use.
  await page
    .getByRole('link', { name: /Sep 15|15 Sep/ })
    .first()
    .click();
  await expect(
    page.getByRole('heading', { name: /AAA decision/ }),
  ).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press('Escape');

  // CSV links exist and the endpoint serves real rows.
  await page.getByRole('button', { name: 'Download CSV' }).click();
  const tradesCsv = page.getByRole('link', { name: 'trades.csv' }).first();
  await expect(tradesCsv).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'decisions.csv' }).first(),
  ).toBeVisible();
  const href = await tradesCsv.getAttribute('href');
  expect(href).toContain('export.csv?kind=trades');
  const csv = await request.get(href!);
  expect(csv.ok()).toBeTruthy();
  expect(await csv.text()).toContain('AAA');

  await page.screenshot({
    path: shot('backtest-panels', testInfo.project.name),
    fullPage: true,
  });
});

test('backtest: form with estimate and cutoff refusal, runs list', async ({
  page,
  request,
}, testInfo) => {
  // The e2e database is shared between runs: remove leftover refused
  // rows from earlier runs of this test, but keep the seeded done run.
  const before = await (await request.get('/api/backtests')).json();
  for (const row of before.runs) {
    if (row.name === 'Too early') {
      await request.delete(`/api/backtests/${row.id}`);
    }
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
    page.getByRole('button', { name: /Too early/ }).first(),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/refused/i).first()).toBeVisible();
  await page.screenshot({ path: shot('backtest-runs', testInfo.project.name) });
});
