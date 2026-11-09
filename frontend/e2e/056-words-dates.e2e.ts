import { expect } from '@playwright/test';

import { test } from './shots';

// 056 step 5 — codes read as words on the reviewed screens. No rendered
// text (outside <code>/<pre>) may contain a snake_case token like
// "agent_exit" or "no_options".

const ROUTES = ['/performance', '/insights', '/backtest', '/inspector'];

for (const route of ROUTES) {
  test(`no snake_case codes on ${route}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop check');
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
    await page
      .locator('.sa-skel')
      .first()
      .waitFor({ state: 'hidden', timeout: 30_000 })
      .catch(() => {});
    const hits = await page.evaluate(() => {
      const re = /\b[a-z]+_[a-z_]+\b/;
      const out: string[] = [];
      const root = document.querySelector('main') ?? document.body;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walker.nextNode())) {
        const el = node.parentElement;
        if (!el || el.closest('code, pre, kbd, samp')) continue;
        const text = node.textContent ?? '';
        const m = re.exec(text);
        if (m) out.push(`${m[0]} :: ${text.trim().slice(0, 100)}`);
      }
      return out;
    });
    expect(hits, `${route} still shows raw codes`).toEqual([]);
  });
}
