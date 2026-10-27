import { readFileSync } from 'node:fs';

import { expect } from '@playwright/test';

import { test } from './shots';

// Only meaningful with the auth backend (E2E_AUTH=1 → SA_AUTH=required
// plus the seeded account). Without it sign-in is off and these run
// nowhere — the default e2e job skips them.
test.skip(process.env.E2E_AUTH !== '1', 'auth e2e needs the E2E_AUTH=1 backend');

const PASSWORD = 'e2e-test-password-14c';

async function fillPasswordAndContinue(page: import('@playwright/test').Page) {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'Authenticator code' })).toBeVisible();
}

// The frozen clock gives every sign-in the same TOTP code — and the
// replay check refuses a used step — so real sign-ins spend one
// recovery code each (codes are single-use; indexed per project so the
// desktop and mobile workers never collide).
let recoveryIndex = 0;

function nextRecoveryCode(project: string): string {
  const codes = readFileSync('/tmp/stock-agent-e2e-data/e2e-recovery-codes', 'utf8')
    .trim()
    .split('\n');
  return codes[(project === 'mobile' ? 5 : 0) + recoveryIndex++];
}

async function signIn(page: import('@playwright/test').Page, project: string) {
  await fillPasswordAndContinue(page);
  await page.getByText('Use a recovery code').click();
  await page.getByLabel('Recovery code').fill(nextRecoveryCode(project));
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL('**/');
}

test('sign-in screen: password, code and locked states', async ({ page }) => {
  // State 1: password.
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByText('Stock Trading Agent').first()).toBeVisible();
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/login-password.png`, fullPage: true });

  // Wrong password: the generic error never says which step failed.
  await page.getByLabel('Password').fill('definitely wrong password');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('alert')).toContainText("check your password or code");
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/login-wrong.png`, fullPage: true });

  // State 2: authenticator code.
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'Authenticator code' })).toBeVisible();
  await expect(page.getByText('Use a recovery code')).toBeVisible();
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/login-code.png`, fullPage: true });

  // State 3 variant: recovery code input.
  await page.getByText('Use a recovery code').click();
  await expect(page.getByLabel('Recovery code')).toBeVisible();
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/login-recovery.png`, fullPage: true });
});

test('session-expired banner and redirect back', async ({ page }, info) => {
  await signIn(page, info.project.name);
  // Drop the session cookie — the next fetch answers 401 and the app
  // redirects to sign-in, then comes back to the same page.
  await page.context().clearCookies();
  await page.goto('/settings');
  await page.waitForURL('**/login?next=%2Fsettings');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/login-redirect.png`, fullPage: true });
});

test('settings Access section and sign out in the sidebar', async ({ page }, info) => {
  await signIn(page, info.project.name);
  await page.goto('/settings');
  await page.getByRole('heading', { name: 'Access' }).scrollIntoViewIfNeeded();
  await expect(page.getByText('required · password + authenticator')).toBeVisible();
  await expect(page.getByText('Signed-in devices')).toBeVisible();
  await expect(page.getByText('This device', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${process.env.SCREENSHOT_DIR ?? '/tmp'}/settings-access.png`, fullPage: true });
  await expect(page.getByRole('button', { name: 'Sign out' }).first()).toBeVisible();
});
