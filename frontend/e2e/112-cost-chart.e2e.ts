import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

/** Item 112 — the Cost per session chart (Insights section 6): its own
 * full-width card, the bars never leave it from 390 to 1920 px, a click
 * or tap selects a bar and the arrow keys step through them
 * (docs/SPEC.md → Model costs → Cost per session). Screenshots land in
 * docs/screenshots/112/ when SCREENSHOT_DIR or E2E_SHOTS is set. */

const outDir = screenshotDir('112');

const WIDTHS = [390, 1024, 1440, 1920];

test('cost per session chart fits and selects', async ({ page }) => {
  await page.goto('/insights');
  const section = page.locator('#q6');
  await expect(section.getByText('What do the models cost?')).toBeVisible({
    timeout: 60_000,
  });
  const card = page.locator('#cost-per-session');
  const chart = card.getByRole('group', { name: /Model cost per session/ });
  const detail = card.locator('[aria-live="polite"]');
  const bars = chart.locator('button');

  for (const period of ['This month', 'All time']) {
    if (period === 'All time') {
      await section.getByRole('button', { name: 'All time' }).click();
      await expect(page).toHaveURL(/costs=all/);
      await expect(card.getByText(/All time · per \w+ · backtests not included/)).toBeVisible();
    }
    await expect(bars.first()).toBeVisible({ timeout: 30_000 });

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(200); // ResizeObserver → label thinning

      const cardBox = await card.boundingBox();
      const chartBox = await chart.boundingBox();
      expect(cardBox).not.toBeNull();
      expect(chartBox).not.toBeNull();
      // The chart's right edge is inside its card — no sideways scroll.
      expect(chartBox!.x + chartBox!.width).toBeLessThanOrEqual(
        cardBox!.x + cardBox!.width + 1,
      );
      const overflow = await chart.evaluate(
        (el) => el.scrollWidth - el.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);

      const count = await bars.count();
      expect(count).toBeGreaterThan(1);
      const selected = await bars.evaluateAll((els) =>
        els.findIndex((el) => el.getAttribute('aria-pressed') === 'true'),
      );
      const target = (selected + 1) % count;

      // Clicking a bar selects it and rewrites the line under the chart.
      const before = (await detail.textContent()) ?? '';
      await bars.nth(target).click();
      await expect(bars.nth(target)).toHaveAttribute('aria-pressed', 'true');
      await expect
        .poll(async () => (await detail.textContent()) ?? '')
        .not.toBe(before);

      // The arrow keys step the selection on.
      await bars.nth(target).focus();
      await page.keyboard.press('ArrowRight');
      const next = (target + 1) % count;
      await expect(bars.nth(next)).toHaveAttribute('aria-pressed', 'true');
      await expect(bars.nth(next)).toBeFocused();
    }
  }
});

test('cost chart screenshots', async ({ page }) => {
  await page.goto('/insights');
  const section = page.locator('#q6');
  await expect(section.getByText('What do the models cost?')).toBeVisible({
    timeout: 60_000,
  });
  const card = page.locator('#cost-per-session');
  await expect(card.getByRole('group', { name: /Model cost per session/ })).toBeVisible({
    timeout: 30_000,
  });
  for (const [period, name] of [
    ['This month', 'this-month'],
    ['All time', 'all'],
  ] as const) {
    if (period === 'All time') {
      await section.getByRole('button', { name: 'All time' }).click();
      await expect(page).toHaveURL(/costs=all/);
      await expect(card.getByText(/All time · per \w+ · backtests not included/)).toBeVisible();
    }
    for (const [width, suffix] of [
      [1440, '1440'],
      [390, '390'],
    ] as const) {
      await page.setViewportSize({ width, height: 900 });
      await card.scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      await card.screenshot({
        path: path.join(outDir, `insights-cost-chart-${name}-${suffix}.png`),
      });
    }
  }
});
