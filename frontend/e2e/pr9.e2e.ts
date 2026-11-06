import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

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
  request,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  // The card tracks ?symbol=. The drawer sections render for any decision,
  // including a rules no_call.
  await page.goto('/?symbol=XOM');

  const card = page.locator('section', {
    has: page.getByRole('heading', { name: 'Latest decision' }),
  });
  const button = page.getByRole('link', { name: 'Open full decision' });
  await expect(button).toBeVisible({ timeout: 60_000 });
  // The card refetches when ?symbol= resolves — wait until it shows XOM's
  // decision before opening the drawer.
  await expect(card.getByText('XOM', { exact: true }).first()).toBeVisible();
  const href = await button.getAttribute('href');
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

  // "Open model call" shows only when the decision reached the model. The
  // card's target may be a rules no_call — live cycles keep writing newer
  // decisions for the same symbol — so open a decision that has a call
  // through the inspector's list instead of assuming the card's target did.
  let modelCallHref: string | null = null;
  await expect(async () => {
    const detail = (await (
      await request.get(`/api${href}`)
    ).json()) as { llm_call_id: number | null };
    if (detail.llm_call_id !== null) {
      modelCallHref = href;
      return;
    }
    const list = (await (
      await request.get('/api/llm-calls?source=live&limit=50')
    ).json()) as { calls: { kind: string; decision_id: number | null }[] };
    const call = list.calls.find(
      (c) => c.kind === 'decision' && c.decision_id !== null
    );
    expect(call, 'no live decision reached the model yet').toBeTruthy();
    modelCallHref = `/decisions/${call!.decision_id}`;
  }).toPass({ timeout: 60_000 });
  if (modelCallHref !== href) {
    await page.goto(modelCallHref!);
  }
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
