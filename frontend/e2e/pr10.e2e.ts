import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR screenshots are for docs/screenshots/pr-10/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched):
// the paused-at-startup header state and the "Started paused" banner.
const outDir = screenshotDir('pr-10');

test('paused at startup: banner, paused pill, one-click resume', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/');

  // The e2e backend is shared across projects: only the first project to
  // run still finds the app in its startup-paused state.
  const banners = await page.evaluate(async () => {
    const res = await fetch('/api/banners');
    const body = await res.json();
    return body.items as { kind: string }[];
  });
  test.skip(
    !banners.some((b) => b.kind === 'paused'),
    'app already resumed by an earlier project'
  );

  // The app always starts paused: an amber Resume in the header, the
  // paused session pill and the blue "Started paused" banner.
  await expect(page.getByRole('button', { name: 'Resume' }).first()).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText('Started paused', { exact: false })).toBeVisible();
  await expect(
    page.getByText('no new entries · stops active', { exact: false })
  ).toBeVisible();
  await page.screenshot({
    path: path.join(outDir, `started-paused-${suffix}.png`),
    fullPage: true,
  });

  // Resume is one click, no reason: banner goes, Pause comes back.
  await page.getByRole('button', { name: 'Resume' }).first().click();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
  await expect(
    page.getByText('Started paused', { exact: false })
  ).toHaveCount(0);
});

test('risk budget card shows meters, facts and hover rules', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/');

  const card = page.locator('section[aria-label="Risk budget"]');
  await expect(card).toBeVisible({ timeout: 60_000 });

  // Three limit meters, each against its configured cap.
  for (const label of ['Open risk', "Today's loss", 'Drawdown']) {
    await expect(card.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(card.getByText('of 2.5% cap')).toBeVisible();
  await expect(card.getByText('of −2% limit')).toBeVisible();
  await expect(card.getByText('of −8% kill')).toBeVisible();

  // The fact rows from the same gate inputs.
  for (const label of [
    'Room for',
    'Sectors',
    'Risk per trade',
    'Shock brake',
    'Day trades',
  ]) {
    await expect(card.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(card.getByText(/more trades?/)).toBeVisible();
  await expect(card.getByText(/last 5 trading days/)).toBeVisible();

  // Every label explains its rule on hover.
  await expect(card.locator('span[title]')).toHaveCount(8);
  await expect(
    card.locator(
      'span[title="The smallest of: positions left, open risk left ÷ risk per trade, and cash ÷ max position size."]'
    )
  ).toBeVisible();

  // The summary tiles sit in their own row: Positions, the model-call
  // counter (calls against the day's cap) and Since start.
  await expect(page.getByText('Positions', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Model calls today', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Since start', { exact: true })).toBeVisible();

  // The daily briefing card sits next to the risk budget card and links
  // to the Reviews screen.
  const briefing = page.locator('section[aria-label="Daily briefing"]');
  await expect(briefing).toBeVisible();
  await expect(
    briefing.getByRole('link', { name: 'All briefings and reviews' })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `risk-budget-${suffix}.png`),
    fullPage: true,
  });
});

test('pause and resume any time, no reason', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/');

  // Reach a known unpaused state first (the previous test may have left
  // the app paused if it ran before the startup-pause test consumed it).
  const header = await page.evaluate(async () => {
    const res = await fetch('/api/header');
    return res.json();
  });
  if (header.paused) {
    await page.getByRole('button', { name: 'Resume' }).first().click();
  }
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible({
    timeout: 60_000,
  });

  // Pause is the same button in reverse — one click, any time. A manual
  // pause shows the paused pill but no "Started paused" banner.
  await page.getByRole('button', { name: 'Pause' }).click();
  await expect(page.getByRole('button', { name: 'Resume' }).first()).toBeVisible();
  await expect(
    page.getByText('Started paused', { exact: false })
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(outDir, `paused-${suffix}.png`),
    fullPage: true,
  });

  // Resume again leaves the app running for the next test file.
  await page.getByRole('button', { name: 'Resume' }).first().click();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
});
