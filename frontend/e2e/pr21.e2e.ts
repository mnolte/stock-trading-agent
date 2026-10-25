import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// PR 21 screenshots are for docs/screenshots/pr-21/ (pass SCREENSHOT_DIR,
// see ./shots), next to the design reference DecisionV2.png. The e2e seed
// writes one decision.v2 row (backend/tests/e2e_seed.py: seed_v2_decision)
// because the e2e run's live prompt stays decision.v1.
const outDir = screenshotDir('pr-21');
const shot = (base: string, project: string) =>
  path.join(outDir, `${base}-${project === 'mobile' ? '390' : '1440'}.png`);

test('decision drawer: v2 options, levels and checks', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByText(/decisions|last session/).first()).toBeVisible({
    timeout: 60_000,
  });

  // The list payload doesn't expose the prompt version — open detail
  // responses until the seeded decision.v2 row turns up.
  const id = await page.evaluate(async () => {
    const res = await fetch('/api/decisions');
    const body = await res.json();
    for (const item of body.items as { id: number }[]) {
      const detail = await fetch(`/api/decisions/${item.id}`).then((r) => r.json());
      if (detail.prompt_version === 'decision.v2') return item.id;
    }
    return null;
  });
  test.skip(id === null, 'no decision.v2 row in the seeded database');

  await page.goto(`/decisions/${id}`);
  await expect(page.getByRole('heading', { name: 'Options offered' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Levels' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Safety gates' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Agent reasoning' })).toBeVisible();

  // The drawer scrolls internally — unclip it so the fullPage shot shows
  // every section (levels list, reasoning, tiles, outcome).
  await page.evaluate(() => {
    for (const el of document.querySelectorAll<HTMLElement>('div')) {
      if (getComputedStyle(el).overflowY === 'auto') {
        el.style.overflowY = 'visible';
        el.style.height = 'auto';
        el.style.maxHeight = 'none';
      }
    }
  });
  await page.screenshot({ path: shot('decision-v2', testInfo.project.name), fullPage: true });
});
