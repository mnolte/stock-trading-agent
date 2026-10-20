import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// Screenshots land in docs/screenshots/<run>/ next to the PR that touched the
// screen. Override with SCREENSHOT_DIR; default keeps them out of design/.
const outDir =
  process.env.SCREENSHOT_DIR ??
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../docs/screenshots/e2e'
  );

test('dashboard watchlist', async ({ page, request }, testInfo) => {
  await page.goto('/');

  const rows = page.locator('[aria-label="Watchlist"] button[role="row"]');
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
