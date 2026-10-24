import path from 'node:path';

import { expect, test } from '@playwright/test';

import { screenshotDir } from './shots';

// PR 18 screenshots are for docs/screenshots/pr-18/ (pass SCREENSHOT_DIR,
// see ./shots), matching the names of the design references:
// Inspector, Inspector-error, Inspector-390.
const outDir = screenshotDir('pr-18');

async function firstCallId(request: import('@playwright/test').APIRequestContext) {
  const page_ = await (await request.get('/api/llm-calls?limit=1')).json();
  return page_.calls[0]?.id as number | undefined;
}

test('LLM inspector: filters, list and call details', async ({ page, request }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/inspector');
  const rows = page.locator('[aria-label="Model calls"] button.sa-row');
  await expect(rows.first()).toBeVisible({ timeout: 60_000 });

  // Defaults: Last session, all outcomes, Live decisions + Reviews.
  await expect(page).toHaveURL(/period=last/);
  await expect(page.locator('[aria-label="Outcome"] [aria-pressed="true"]')).toHaveText(/All/);
  await expect(
    page.locator('[aria-label="Source · pick one or more"] [aria-pressed="true"]'),
  ).toHaveCount(2);

  await page.screenshot({
    path: path.join(outDir, `Inspector-${suffix}.png`),
    fullPage: true,
  });

  // A filter change is written to the URL and survives a reload.
  await page.locator('[aria-label="Outcome"] button', { hasText: 'Error' }).click();
  await expect(page).toHaveURL(/outcome=error/);
  await page.reload();
  await expect(page.locator('[aria-label="Outcome"] [aria-pressed="true"]')).toHaveText(/Error/);

  // A direct ?call= link opens the call's details.
  const callId = await firstCallId(request);
  expect(callId).toBeDefined();
  await page.goto(`/inspector?call=${callId}`);
  await expect(
    page.getByRole('heading', { name: 'Prompt', exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/hash (matches|differs)/)).toBeVisible();

  // Keyboard walkthrough: Tab reaches a call row, Enter selects it.
  await page.goto('/inspector');
  await expect(rows.first()).toBeVisible();
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press('Tab');
    const pressed = await page.evaluate(
      () => document.activeElement?.classList.contains('sa-row') ?? false,
    );
    if (pressed) break;
  }
  const focused = page.locator('button.sa-row:focus');
  await expect(focused).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/call=\d+/);
});

test('LLM inspector: error call shows the error block', async ({ page, request }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  // The e2e seed only creates error rows when a call failed; skip cleanly
  // when every recorded call succeeded.
  const summary = await (
    await request.get('/api/llm-calls/summary?source=live,shadow,review')
  ).json();
  if ((summary.options.outcome.error ?? 0) === 0) {
    test.skip(true, 'no error calls in the seed');
    return;
  }
  const errors = await (
    await request.get('/api/llm-calls?outcome=error&limit=1')
  ).json();
  const callId = errors.calls[0].id as number;

  await page.goto(`/inspector?call=${callId}&outcome=error`);
  await expect(
    page.getByRole('heading', { name: 'Error', exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/request id|timed out|HTTP \d+/).first()).toBeVisible();
  // No raw response on a transport failure.
  await expect(
    page.getByRole('heading', { name: 'Raw response', exact: true }),
  ).not.toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `Inspector-error-${suffix}.png`),
    fullPage: true,
  });
});
