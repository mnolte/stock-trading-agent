import path from 'node:path';
import { expect } from '@playwright/test';
import { screenshotDir, test } from './shots';

// Ops PR screenshot: a first-seen ERROR fingerprint raises the "New error"
// warning notification. The client-error endpoint is the honest trigger —
// it logs at ERROR like the real React boundary.
const outDir = screenshotDir('ops-alerts');

test('new error notification', async ({ page, request, baseURL }) => {
  await request.post(`${baseURL}/api/logs/client`, {
    data: {
      message: 'e2e shot: chart render blew up',
      error_id: 'e-e2e-shot1',
      tab_id: 't-e2e',
      url: 'http://localhost/',
    },
  });
  await page.goto('/settings');
  const notify = page.locator('section#notify');
  const row = notify.getByText(/New error: e2e shot/).first();
  await expect(row).toBeVisible({ timeout: 60_000 });
  await notify.scrollIntoViewIfNeeded();
  await notify.screenshot({ path: path.join(outDir, 'new-error.png') });
});
