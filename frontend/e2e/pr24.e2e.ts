import { readFileSync } from 'node:fs';

import { expect } from '@playwright/test';

import { test } from './shots';

// PR 24: the Server section exists only on the VPS sign-in build
// (E2E_AUTH=1 → SA_AUTH=required with the seeded account).
test.skip(process.env.E2E_AUTH !== '1', 'auth e2e needs the E2E_AUTH=1 backend');

const PASSWORD = 'e2e-test-password-14c';
const SHOT_DIR = process.env.SCREENSHOT_DIR ?? '/tmp';

let recoveryIndex = 0;

function nextRecoveryCode(): string {
  const codes = readFileSync('/tmp/stock-agent-e2e-data/e2e-recovery-codes', 'utf8')
    .trim()
    .split('\n');
  return codes[recoveryIndex++];
}

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login');
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByText('Use a recovery code').click();
  await page.getByLabel('Recovery code').fill(nextRecoveryCode());
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL('**/');
}

test('Settings → Server renders the design rows', async ({ page }) => {
  await signIn(page);
  await page.goto('/settings');
  await page.getByRole('link', { name: 'Server' }).click();
  const server = page.locator('#server');
  await expect(
    server.getByRole('heading', { name: 'This server' }),
  ).toBeVisible();
  await expect(server.getByText(/Disk/)).toBeVisible();
  await expect(server.getByText(/Local copy/)).toBeVisible();
  await expect(server.getByText(/Heartbeat/)).toBeVisible();
  await expect(server.getByText(/instance sa-/)).toBeVisible();
  await expect(
    server.getByRole('button', { name: 'Back up now' }),
  ).toBeVisible();
  await expect(
    server.getByRole('button', { name: 'Download latest backup' }),
  ).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/settings-server.png`,
    fullPage: true,
  });
});

test('Server section collapses without sign-in build', async () => {
  // The local build shows only the description line — the panel's data
  // comes from /api/server, which answers 404 locally; covered by the
  // backend test, kept here as a documented non-case.
  test.skip();
});
