import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR screenshots are for docs/screenshots/pr-9/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched):
// the latest-decision card on Main and the rebuilt decision drawer.
const outDir = screenshotDir('pr-9');

test('latest decision card: chips, summary box, full-decision button', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/');

  const card = page.locator('section', {
    has: page.getByRole('heading', { name: 'Latest decision' }),
  });
  await expect(card).toBeVisible({ timeout: 60_000 });

  // Market regime and earnings chips.
  await expect(card.getByText(/market (up|down|sideways) · (low|normal|high) vol/)).toBeVisible();
  await expect(card.getByText(/earnings/)).toBeVisible();

  // The three-line summary box and the renamed button.
  const button = card.getByRole('link', { name: 'Open full decision' });
  await expect(button).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `main-${suffix}.png`),
    fullPage: true,
  });
});

test('decision drawer: gates, signal stack, reasoning, outcome', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  // The card tracks ?symbol= and the default symbol's latest decision is a
  // rules no_call — no model call, so no "Open model call" link. XOM's
  // seeded decision did reach the model.
  await page.goto('/?symbol=XOM');

  const card = page.locator('section', {
    has: page.getByRole('heading', { name: 'Latest decision' }),
  });
  const button = page.getByRole('link', { name: 'Open full decision' });
  await expect(button).toBeVisible({ timeout: 60_000 });
  // The card refetches when ?symbol= resolves — wait until it shows XOM's
  // decision before opening the drawer.
  await expect(card.getByText('XOM', { exact: true }).first()).toBeVisible();
  await button.click();
  await expect(page).toHaveURL(/\/decisions\/\d+/);

  // Drawer sections from design/screens/Decision.dc.html.
  for (const name of [
    'Safety gates',
    'Signal stack',
    'Agent reasoning',
    'Outcome',
  ]) {
    await expect(
      page.getByRole('heading', { name, exact: true })
    ).toBeVisible();
  }

  // Signal stack has a 1h and a Daily column.
  await expect(page.getByText('RSI 14')).toBeVisible();
  await expect(
    page.getByRole('columnheader', { name: '1h' }).or(page.getByText('1h').first())
  ).toBeVisible();

  // Level tiles.
  for (const label of ['Entry', 'Stop', 'Target']) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText('Invalidation · 1h close')).toBeVisible();

  // Footer: order status and links.
  await expect(
    page.getByRole('link', { name: 'Open position & orders' })
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Open model call' })
  ).toBeVisible();

  // The raw input snapshot no longer appears on this screen.
  await expect(
    page.getByRole('heading', { name: 'Input snapshot' })
  ).not.toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `decision-${suffix}.png`),
    fullPage: true,
  });

  // "Open model call" lands on this call in the inspector, where the raw
  // input snapshot lives.
  await page.getByRole('link', { name: 'Open model call' }).click();
  await expect(page).toHaveURL(/\/inspector\?call=\d+/);
  await expect(page.getByText('User · input snapshot')).toBeVisible();
});
