import os from 'node:os';
import path from 'node:path';
import { appendFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { test as base, type Page } from '@playwright/test';

/** Where a spec writes its screenshots. By default a temp folder, so a
 * normal `make test` can never overwrite an earlier PR's screenshots; pass
 * SCREENSHOT_DIR (e.g. `SCREENSHOT_DIR=../docs/screenshots/pr-14`) to write
 * a PR's screenshots into docs/screenshots/pr-<n>/ on purpose. */
export function screenshotDir(pr: string): string {
  const explicit = process.env.SCREENSHOT_DIR;
  if (explicit) return path.resolve(explicit);
  return path.join(os.tmpdir(), 'stock-agent-e2e-screenshots', pr);
}

/** Screenshots are PR documentation, not assertions — nobody looks at the
 * temp-dir copies, so a plain `playwright test` skips the encode+write.
 * Pass SCREENSHOT_DIR or E2E_SHOTS=1 to take them for real. */
const shotsWanted = Boolean(
  process.env.SCREENSHOT_DIR || process.env.E2E_SHOTS,
);

// E2E_COVERAGE=1 (CI only, item 088) collects Chromium V8 coverage for
// every page of the context. Raw entries land in coverage-e2e/raw/ as
// JSONL; scripts/coverage-e2e.mjs converts them to istanbul reports.
const e2eCoverage = process.env.E2E_COVERAGE === '1';
const coverageRawDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../coverage-e2e/raw',
);

/** Drop-in `test` for every e2e spec: page.screenshot becomes a no-op
 * unless screenshots were asked for. */
export const test = base.extend({
  // `run` is the playwright fixture callback — named so the react-hooks
  // lint rule doesn't take it for a hook call.
  page: async ({ page }, run) => {
    if (!shotsWanted) {
      page.screenshot = (() =>
        Promise.resolve(Buffer.alloc(0))) as typeof page.screenshot;
    }
    await run(page);
  },
  context: [
    async ({ context }, run, testInfo) => {
      if (!e2eCoverage) {
        await run(context);
        return;
      }
      type V8Entry = Awaited<
        ReturnType<Page['coverage']['stopJSCoverage']>
      >[number];
      const entries: V8Entry[] = [];
      const stopping = new Set<Promise<void>>();
      const collected = new Set<Page>();
      // The page fixture closes pages before this fixture tears down, and a
      // closed target refuses stopJSCoverage — collect when each page
      // closes instead. stopOnce also covers pages the test closes itself.
      const collect = (page: Page) => {
        if (collected.has(page)) return;
        collected.add(page);
        const pending = page.coverage
          .stopJSCoverage()
          .then((pageEntries) => {
            entries.push(...pageEntries);
          })
          .catch((error: unknown) => {
            console.warn(
              `e2e coverage: stopJSCoverage failed for ${testInfo.testId}: ${String(error)}`,
            );
          });
        stopping.add(pending);
        void pending.finally(() => stopping.delete(pending));
      };
      const newPage = context.newPage.bind(context);
      context.newPage = async () => {
        const page = await newPage();
        await page.coverage.startJSCoverage({ resetOnNavigation: false });
        const stopOnce = () => collect(page);
        const close = page.close.bind(page);
        page.close = (async (options?: Parameters<Page['close']>[0]) => {
          stopOnce();
          return close(options);
        }) as typeof page.close;
        page.on('close', stopOnce);
        return page;
      };
      try {
        await run(context);
      } finally {
        // Pages left open (created but never closed) still hold coverage.
        for (const page of context.pages()) collect(page);
        await Promise.allSettled([...stopping]);
        if (entries.length > 0) {
          mkdirSync(coverageRawDir, { recursive: true });
          const file = path.join(
            coverageRawDir,
            `${testInfo.project.name}-${testInfo.workerIndex}.jsonl`,
          );
          appendFileSync(
            file,
            `${JSON.stringify({ testId: testInfo.testId, entries })}\n`,
          );
        }
      }
    },
    { auto: true },
  ],
});
