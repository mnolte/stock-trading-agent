import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect } from '@playwright/test';

import { test } from './shots';

// 084 screenshots: Settings → Server shows the Image row (digest +
// "signature verified at deploy" / amber "not verified") and the
// Backups "Delete protection" row. Needs the E2E_AUTH=1 backend —
// /api/server answers 404 without sign-in — plus SA_IMAGE and
// SA_IMAGE_VERIFIED on it so the Image row has a digest to show.
const PASSWORD = 'e2e-test-password-14c';
const SHOT_DIR = process.env.SCREENSHOT_DIR ?? '/tmp';
const DATA_DIR = process.env.E2E_DATA_DIR ?? '/tmp/stock-agent-e2e-data';

let recoveryIndex = 0;

function nextRecoveryCode(): string {
  const codes = readFileSync(join(DATA_DIR, 'e2e-recovery-codes'), 'utf8')
    .trim()
    .split('\n');
  return codes[recoveryIndex++];
}

test.skip(process.env.E2E_AUTH !== '1', 'auth e2e needs the E2E_AUTH=1 backend');

test('084: Settings → Server shows image and delete protection', async ({
  page,
}) => {
  await page.goto('/login');
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByText('Use a recovery code').click();
  await page.getByLabel('Recovery code').fill(nextRecoveryCode());
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL('**/');

  await page.goto('/settings/server');
  const server = page.locator('#server');
  // The Image row's right side: short digest + verification state
  // (SA_IMAGE/SA_IMAGE_VERIFIED are set on the e2e backend for this).
  await expect(
    server.getByText(/signature verified at deploy|not verified/),
  ).toBeVisible({ timeout: 60_000 });
  await expect(server.getByText('Delete protection')).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/settings-server.png`,
    fullPage: true,
  });
});
