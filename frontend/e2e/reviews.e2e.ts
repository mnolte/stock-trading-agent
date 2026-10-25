import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR screenshots are for docs/screenshots/pr-9/ (pass SCREENSHOT_DIR, see ./shots) (design/ stays untouched):
// the Reviews screen (Weekly / Briefings tabs, verdicts to confirm) and the
// verdict row in the decision drawer.
const outDir = screenshotDir('pr-9');

test('reviews screen: weekly tab, review detail, verdicts to confirm', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/reviews');

  // Sidebar entry lands on the page; schedule line, tabs and list visible.
  await expect(
    page.getByText('Weekly review Saturday 10:00', { exact: false })
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('tab', { name: 'Weekly' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Briefings' })).toBeVisible();

  // The newest review auto-opens: heading, stat tiles and the verdicts card.
  await expect(
    page.getByRole('heading', { name: /Week \d+ ·/ })
  ).toBeVisible();
  await expect(page.getByText('Trades closed').first()).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Verdicts to confirm' })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `reviews-${suffix}.png`),
    fullPage: true,
  });
});

test('briefings tab', async ({ page }, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  await page.goto('/reviews');
  await page.getByRole('tab', { name: 'Briefings' }).click();
  await expect(
    page.getByRole('heading', { name: 'Verdicts to confirm' })
  ).toBeVisible({ timeout: 60_000 });
  // A briefing detail renders its day title and the fact tiles.
  await expect(
    page.getByText('Open positions', { exact: true })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `briefings-${suffix}.png`),
    fullPage: true,
  });
});

test('decision drawer shows the verdict row', async ({
  page,
  request,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';

  // The latest decision may be a rules no_call with no verdict; open a
  // decision that has one instead.
  const verdicts = await request.get('/api/verdicts?status=all');
  const items: { decision_id: number }[] = (await verdicts.json()).items;
  test.skip(items.length === 0, 'no verdicts seeded in the e2e database');
  const decisionId = items[0].decision_id;

  await page.goto(`/decisions/${decisionId}`);

  await expect(
    page.getByRole('heading', { name: 'Outcome', exact: true })
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Draft verdict:|Verdict:/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Good call' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Bad call' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Unclear' })).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Verdicts in Reviews' })
  ).toBeVisible();

  await page.screenshot({
    path: path.join(outDir, `decision-verdict-${suffix}.png`),
    fullPage: true,
  });
});
