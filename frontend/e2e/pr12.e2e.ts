import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 12 screenshots are for docs/screenshots/pr-12/ (pass SCREENSHOT_DIR, see ./shots); design/ is untouched.
const outDir = screenshotDir('pr-12');

const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('main: watchlist filter tabs, search and Show all', async ({
  page,
  request,
}, testInfo) => {
  await page.goto('/');

  const section = page.locator('section').filter({
    has: page.getByRole('heading', { name: 'Watchlist', exact: true }),
  });
  const rows = section.locator('button[role="row"]');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });

  // The e2e database is shared: whichever list cycles currently run —
  // fixed (all rows) or screened (watchlist_rows, then "Show all") —
  // decides the expected count.
  const wl = await (await request.get('/api/watchlist')).json();
  const screened = wl.universe_mode === 'screened';
  const cap = wl.watchlist_rows ?? 15;
  const expected = screened
    ? Math.min(cap, wl.rows.length)
    : wl.rows.length;
  await expect(rows).toHaveCount(expected);

  // Filter tabs with counts (design/screens/Main.dc.html).
  const tablist = section.getByRole('tablist', { name: 'Filter watchlist' });
  await expect(tablist).toBeVisible();
  await expect(tablist.getByRole('tab', { name: /All/ })).toBeVisible();
  await expect(tablist.getByRole('tab', { name: /Pass rules/ })).toBeVisible();
  await expect(tablist.getByRole('tab', { name: /Held/ })).toBeVisible();
  await expect(
    tablist.getByRole('tab', { name: /Earnings ≤ 3 days/ })
  ).toBeVisible();

  // The Held tab selects and narrows the table to held rows only.
  const heldTab = tablist.getByRole('tab', { name: /^Held/ });
  await heldTab.click();
  await expect(heldTab).toHaveAttribute('aria-selected', 'true');
  for (const row of await rows.all()) {
    await expect(row).toContainText('Held');
  }
  await tablist.getByRole('tab', { name: /All/ }).click();
  await expect(rows).toHaveCount(expected);

  // Search narrows to a case-insensitive substring match.
  const find = section.getByLabel('Find a symbol');
  await find.fill('aap');
  await expect(rows.first()).toContainText('AAPL');
  expect(await rows.count()).toBeGreaterThanOrEqual(1);
  await find.fill('');
  await expect(rows).toHaveCount(expected);

  // The muted rank hint from the design, and the Universe link.
  await expect(
    section.getByText(/sorted by rank, 15 rows, then Show all/i)
  ).toBeVisible();
  await expect(
    section.getByRole('link', { name: 'Universe' })
  ).toBeVisible();

  // Screened mode only: beyond watchlist_rows a "Show all" button
  // expands the table.
  if (screened && wl.rows.length > cap) {
    const showAll = section.getByRole('button', { name: /Show all/ });
    await expect(showAll).toBeVisible();
    await showAll.click();
    await expect(rows).toHaveCount(wl.rows.length);
    await expect(
      section.getByRole('button', { name: 'Show fewer' })
    ).toBeVisible();
  }

  await page.screenshot({
    path: shot('main', testInfo.project.name),
    fullPage: true,
  });
});
