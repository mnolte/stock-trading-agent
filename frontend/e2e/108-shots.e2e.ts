import path from 'node:path';

import { expect } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 108 screenshots are for docs/screenshots/108/ (pass SCREENSHOT_DIR).
// performance-curve-hover-1440 shows the equity curve with the crosshair
// tooltip: adjusted equity on the line, raw paper equity underneath.
const outDir = screenshotDir('108');

test('performance: curve with hover tooltip', async ({ page }, testInfo) => {
  await page.goto('/performance');
  await expect(
    page.getByRole('heading', { name: 'Performance', level: 1 }),
  ).toBeVisible({ timeout: 60_000 });
  const chart = page.getByTestId('equity-chart');
  await expect(chart).toBeVisible();
  // Let the canvas lay out, then hover the middle of the plot so the
  // crosshair snaps to a session point and the tooltip appears.
  await page.waitForTimeout(800);
  const box = await chart.boundingBox();
  if (!box) throw new Error('equity chart has no box');
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await expect(page.getByTestId('equity-hover')).toBeVisible({
    timeout: 10_000,
  });
  await page.screenshot({
    path: path.join(
      outDir,
      `performance-curve-hover-${testInfo.project.name === 'mobile' ? '390' : '1440'}.png`,
    ),
    fullPage: false,
  });
});
