import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 16 screenshots are for docs/screenshots/pr-16/ (pass SCREENSHOT_DIR,
// see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-16');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

/** Close notification toasts left over from earlier specs in the shared
 * e2e database so they don't cover the screenshot. */
async function dismissToasts(page: import('@playwright/test').Page) {
  const toasts = page.getByRole('button', { name: 'Dismiss' });
  while (await toasts.count()) {
    await toasts.first().click();
  }
}

test('readiness: verdict, criteria groups, manual items and history', async ({
  page,
  request,
}, testInfo) => {
  // Start from a known manual-item state so the tick below is deterministic
  // (the e2e database is shared between runs).
  await request.post('/api/readiness/manual', {
    data: { item: 'tax', done: false, note: 'e2e reset' },
  });

  await page.goto('/readiness');

  // The scheduler stores one snapshot per closed session; the first tick
  // after startup may take a minute, so wait on the verdict banner.
  await expect(page.getByText('Not ready for live trading')).toBeVisible({
    timeout: 90_000,
  });
  await expect(
    page.getByText(/nothing unlocks automatically/),
  ).toBeVisible();
  await expect(
    page.getByText(/Checked after the close|No snapshot yet/),
  ).toBeVisible();

  // Summary tiles.
  await expect(
    page.getByText('Pass', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('Not yet', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('Waiting for data', { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole('region', { name: 'Readiness summary' })
      .getByText('Manual items', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Earliest possible')).toBeVisible();

  // All 15 criteria across the three groups, each with value, target,
  // bar and a status chip. The first snapshot lands on the first
  // readiness tick (once a minute), so give the first row room.
  await expect(page.getByText('Closed trades', { exact: true })).toBeVisible({
    timeout: 90_000,
  });
  for (const group of ['Evidence', 'Model', 'Operations']) {
    await expect(
      page.getByText(group, { exact: true }).first(),
    ).toBeVisible();
  }
  for (const label of [
    'Paper sessions in this run',
    'Return vs SPY (adjusted)',
    'Max drawdown',
    'Average R (adjusted)',
    'Profit factor',
    'Beats rules-only',
    'Entry proposals that pass code checks',
    'Confidence calibration',
    'Cycles completed',
    'Positions without a stop',
    'Kill switch events',
    'Unresolved errors',
    'Price data quality',
    'Reconciliation',
  ]) {
    await expect(
      page.getByText(label, { exact: true }),
    ).toBeVisible();
  }

  // The seeded run is a week old: count criteria miss their target and the
  // sample-bound criteria wait for data — all three chips show.
  await expect(page.getByText('waiting for data').first()).toBeVisible();

  // Manual items: tick needs a note, lands in History.
  await expect(page.getByText('0 of 5 done')).toBeVisible();
  await page
    .getByRole('checkbox', { name: 'Dutch tax treatment checked' })
    .click();
  await expect(
    page.getByText(/Tick “Dutch tax treatment checked”/),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('A short note is required.')).toBeVisible();
  await page
    .getByLabel('Note')
    .fill('Checked with the advisor on 12 October.');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('1 of 5 done')).toBeVisible();
  await expect(page.getByText('done', { exact: true }).first()).toBeVisible();

  // History: the run start plus the manual change, dated.
  await expect(
    page.getByText('Run started; all counters reset'),
  ).toBeVisible();
  await expect(
    page.getByText(/Dutch tax treatment checked ticked/).first(),
  ).toBeVisible();

  await dismissToasts(page);
  await page.screenshot({
    path: shot('readiness', testInfo.project.name),
    fullPage: true,
  });
});

test('readiness: settings thresholds and fixed criteria note', async ({
  page,
}, testInfo) => {
  await page.goto('/settings');
  const section = page.locator('section#ready');
  await expect(
    section.getByRole('heading', { name: 'Readiness' }),
  ).toBeVisible({ timeout: 60_000 });

  // The nine designed fields; zero-tolerance criteria and manual items
  // have no field — the note says they are fixed by the spec.
  for (const label of [
    'Closed trades',
    'Paper sessions',
    'Max drawdown under',
    'Average R at least',
    'Profit factor at least',
    'Proposals passing code checks',
    'Calibration gap within',
    'Cycles completed',
    'Price checks blocked at most',
  ]) {
    await expect(section.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(
    section.getByText(/fixed by the spec/),
  ).toBeVisible();

  await dismissToasts(page);
  await section.screenshot({
    path: shot('settings-readiness', testInfo.project.name),
  });
});
