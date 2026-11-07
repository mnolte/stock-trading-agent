import path from 'node:path';
import { screenshotDir, test } from './shots';

// 046 audit screenshots — remaining routes not covered by 045-screens.
const outDir = screenshotDir('046');
const suffix = (project: string) => (project === 'mobile' ? '390' : '1440');

async function shot(page: import('@playwright/test').Page, name: string) {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

for (const route of ['performance', 'readiness', 'logs', 'strategies', 'backtest']) {
  test(route, async ({ page }, testInfo) => {
    await page.goto(`/${route}`);
    await shot(page, `${route}-${suffix(testInfo.project.name)}.png`);
  });
}
