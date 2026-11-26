import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect } from '@playwright/test';

import { test } from './shots';

// Item 081 screenshots: Settings → Server shows the heartbeat URL
// masked (write-only secret, saved through "Confirm it is you"), and
// the Needs you card shows the stored-error-text item. Needs the
// E2E_AUTH=1 backend.
test.skip(process.env.E2E_AUTH !== '1', 'auth e2e needs the E2E_AUTH=1 backend');

const PASSWORD = 'e2e-test-password-14c';
const HB_URL = 'https://hc-ping.com/abc123f9a';
const SHOT_DIR = process.env.SCREENSHOT_DIR ?? '/tmp';
const DATA_DIR = process.env.E2E_DATA_DIR ?? '/tmp/stock-agent-e2e-data';
const BACKEND = join(
  dirname(fileURLToPath(import.meta.url)),
  '../../backend',
);

let recoveryIndex = 0;

function nextRecoveryCode(): string {
  const codes = readFileSync(join(DATA_DIR, 'e2e-recovery-codes'), 'utf8')
    .trim()
    .split('\n');
  return codes[recoveryIndex++];
}

function seed081(cleanup = false) {
  execFileSync(
    '../.venv/bin/python',
    ['-m', 'tests.e2e_081_seed', ...(cleanup ? ['--cleanup'] : [])],
    {
      cwd: BACKEND,
      env: {
        ...process.env,
        MODE: 'simulated',
        SA_AUTH: 'required',
        SA_DB_PATH: process.env.E2E_DB_PATH ?? '/tmp/stock-agent-e2e.db',
        SA_DATA_DIR: DATA_DIR,
      },
      stdio: 'pipe',
    },
  );
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

test.beforeAll(() => seed081());
test.afterAll(() => {
  try {
    seed081(true);
  } catch {
    // best effort: a leftover event only adds a Needs you row
  }
});

test('081: Needs you shows the stored error text item', async ({ page }) => {
  await signIn(page);
  await page.reload();
  await expect(
    page.getByText('A key appeared in stored error text'),
  ).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/needs-you-stored-scrub.png`,
    fullPage: true,
  });
});

test('081: Settings → Server shows the masked heartbeat', async ({
  page,
}) => {
  await signIn(page);
  await page.goto('/settings/server');
  const server = page.locator('#server');
  // Save through the real path: Set…, the URL, Save, then the
  // "Confirm it is you" dialog asks for the password again.
  // A reused scratch database may already hold the URL — the button
  // reads Set… or Replace… depending on configured state.
  await server.getByRole('button', { name: /Set…|Replace…/ }).click();
  await server.getByLabel('Heartbeat URL').fill(HB_URL);
  await server.getByRole('button', { name: 'Save', exact: true }).click();
  await page.locator('#confirm-secret').fill(PASSWORD);
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(server.getByText(/hc-ping\.com\/••••/)).toBeVisible();
  await expect(
    server.getByRole('button', { name: 'Replace…' }),
  ).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/settings-server-heartbeat.png`,
    fullPage: true,
  });
});
