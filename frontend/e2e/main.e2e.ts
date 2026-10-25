import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// Screenshots for docs/screenshots/e2e/ (pass SCREENSHOT_DIR, see ./shots).
const outDir = screenshotDir('e2e');

test('dashboard watchlist', async ({ page, request }, testInfo) => {
  await page.goto('/');

  const rows = page.locator('[aria-label="Watchlist"] button.sa-row');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });
  // The e2e database is shared between suites: in fixed mode every row
  // renders, in screened mode the first watchlist_rows do.
  const wl = await (await request.get('/api/watchlist')).json();
  const expected =
    wl.universe_mode === 'screened'
      ? Math.min(wl.watchlist_rows ?? 15, wl.rows.length)
      : wl.rows.length;
  await expect(rows).toHaveCount(expected);
  await expect(rows.filter({ hasText: 'SPY' })).toHaveCount(1);

  const name = testInfo.project.name === 'mobile' ? 'main-390.png' : 'main-1440.png';
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
});
