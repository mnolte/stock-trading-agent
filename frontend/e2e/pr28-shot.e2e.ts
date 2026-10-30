import { expect } from '@playwright/test';
import { screenshotDir, test } from './shots';
// PR 28 screenshot: Settings → Email (SMTP), password required after a host change.
test('smtp password required after host change', async ({ page }) => {
  await page.goto('/settings');
  const notify = page.locator('section#notify');
  await expect(notify.locator('#smtp-host')).toBeVisible({ timeout: 60_000 });
  await notify.locator('#smtp-host').fill('mail.saved.example');
  await notify.locator('#smtp-user').fill('user@saved.example');
  await notify.locator('#smtp-from').fill('agent@saved.example');
  await notify.locator('#smtp-to').fill('marc@example.com');
  await notify.locator('#smtp-pass').fill('first-password');
  await notify.getByRole('button', { name: /Test and save/i }).click();
  await expect(notify.locator('#smtp-host')).toHaveValue('mail.saved.example', { timeout: 15_000 });
  await notify.locator('#smtp-host').fill('mail.other.example');
  await expect(notify.getByText('required — server changed')).toBeVisible();
  await page.screenshot({ path: `${screenshotDir('pr-28')}/smtp-password-required.png` });
});
