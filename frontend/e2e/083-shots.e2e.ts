import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect } from '@playwright/test';

import { test } from './shots';

// Item 083 screenshots + assertions: the paused sign-in states (a known
// browser is let through, an unknown one sees only the pause) and
// Settings → Access → Known browsers. Needs the E2E_AUTH=1 backend —
// the pause itself is seeded by tests/e2e_083_seed.py.
test.skip(process.env.E2E_AUTH !== '1', 'auth e2e needs the E2E_AUTH=1 backend');

const PASSWORD = 'e2e-test-password-14c';
const SHOT_DIR = process.env.SCREENSHOT_DIR ?? '/tmp';
const DATA_DIR = process.env.E2E_DATA_DIR ?? '/tmp/stock-agent-e2e-data';
const BACKEND = join(
  dirname(fileURLToPath(import.meta.url)),
  '../../backend',
);

// Recovery codes are single-use and shared with pr23.e2e.ts in the same
// invocation: it spends index 0-1 (desktop) and 5-6 (mobile), so this
// spec takes 2-3 and 7-8.
let recoveryIndex = 0;

function nextRecoveryCode(project: string): string {
  const codes = readFileSync(join(DATA_DIR, 'e2e-recovery-codes'), 'utf8')
    .trim()
    .split('\n');
  const base = project === 'mobile' ? 7 : 2;
  return codes[base + recoveryIndex++];
}

function seed083(cleanup = false) {
  execFileSync(
    '../.venv/bin/python',
    ['-m', 'tests.e2e_083_seed', ...(cleanup ? ['--cleanup'] : [])],
    {
      cwd: BACKEND,
      env: {
        ...process.env,
        MODE: 'simulated',
        SA_AUTH: 'required',
        SA_DB_PATH: process.env.E2E_DB_PATH ?? '/tmp/stock-agent-e2e.db',
        SA_DATA_DIR: DATA_DIR,
        SA_SIM_NOW: '2026-10-15T17:00:00Z',
      },
      stdio: 'pipe',
    },
  );
}

async function signIn(page: import('@playwright/test').Page, project: string) {
  await page.goto('/login');
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByText('Use a recovery code').click();
  await page.getByLabel('Recovery code').fill(nextRecoveryCode(project));
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  try {
    await page.waitForURL('**/', { timeout: 15_000 });
  } catch {
    const alert = await page
      .getByRole('alert')
      .textContent()
      .catch(() => null);
    throw new Error(
      `sign-in did not complete: url=${page.url()} alert=${alert}`,
    );
  }
}

test.beforeAll(() => {
  // A clean slate: no pause, no leftover lockout attempts.
  try {
    seed083(true);
  } catch {
    // first run on a fresh database: the seed still clears attempts
  }
});

test.afterAll(() => {
  try {
    seed083(true);
  } catch {
    // best effort: a leftover pause only affects later auth specs
  }
});

test('083: sign-in states — password, code, locked', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/login-password.png`,
    fullPage: true,
  });

  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(
    page.getByRole('heading', { name: 'Authenticator code' }),
  ).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/login-code.png`,
    fullPage: true,
  });
  // Back to the password step for the lockout.
  await page.getByRole('button', { name: 'Back' }).click();

  // Five wrong passwords from this address, then one more: the 429
  // locked state is the wait-time error rendered in place.
  for (let i = 0; i < 6; i++) {
    await page.getByLabel('Password').fill(`wrong password ${i}`);
    await page.getByRole('button', { name: 'Continue' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
  }
  await expect(page.getByRole('alert')).toContainText('Try again in');
  await page.screenshot({
    path: `${SHOT_DIR}/login-locked.png`,
    fullPage: true,
  });
});

test('083: paused sign-in — known browser vs unknown', async ({
  page,
  context,
}, info) => {
  // A completed sign-in mints the device cookie; the session itself is
  // not needed for the paused states. The previous test's lockout
  // attempts must go first — the address would still be locked.
  seed083(true);
  await signIn(page, info.project.name);

  // Pause sign-in globally, then keep only the device cookie — the
  // session cookie would redirect /login back into the app.
  seed083();
  const cookies = await context.cookies();
  const device = cookies.find(
    (c) => c.name === 'sa_device' || c.name === '__Host-sa_device',
  );
  expect(device, 'device cookie after sign-in').toBeTruthy();
  await context.clearCookies();
  await context.addCookies([device!]);

  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  // The amber note: this browser is known, the form stays usable.
  await expect(
    page.getByText('This browser is known, so you can sign in'),
  ).toBeVisible();
  // The form stays usable: with a password typed, Continue enables.
  await page.getByLabel('Password').fill(PASSWORD);
  await expect(
    page.getByRole('button', { name: 'Continue' }),
  ).toBeEnabled();
  await page.screenshot({
    path: `${SHOT_DIR}/login-paused-known.png`,
    fullPage: true,
  });

  // An unknown browser sees only the pause and its end time — the
  // whole form is disabled.
  await context.clearCookies();
  await page.goto('/login');
  await expect(
    page.getByText('Sign-in is paused until', { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel('Password')).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Continue' }),
  ).toBeDisabled();
});

test('083: settings access lists known browsers', async ({ page }, info) => {
  // Pause off again for a normal sign-in (a fresh context per test has
  // no device cookie, so the global pause would refuse it).
  seed083(true);
  await signIn(page, info.project.name);
  await page.goto('/settings/access');
  await page.getByRole('heading', { name: 'Access' }).scrollIntoViewIfNeeded();
  await expect(page.getByText('Known browsers')).toBeVisible();
  await expect(page.getByText('this browser').first()).toBeVisible();
  await expect(
    page.getByText('At most 10. A password change'),
  ).toBeVisible();
  await page.screenshot({
    path: `${SHOT_DIR}/settings-access.png`,
    fullPage: true,
  });
});
