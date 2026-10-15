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

test('dashboard watchlist', async ({ page }, testInfo) => {
  await page.goto('/');

  const rows = page.locator('[aria-label="Watchlist"] button[role="row"]');
  await expect(rows).toHaveCount(10, { timeout: 60_000 });
  await expect(rows.first()).toContainText('SPY');

  const name = testInfo.project.name === 'mobile' ? 'main-390.png' : 'main-1440.png';
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
});
