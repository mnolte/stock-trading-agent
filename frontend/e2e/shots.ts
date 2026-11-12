import os from 'node:os';
import path from 'node:path';
import { appendFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { test as base } from '@playwright/test';

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
// every page. Raw entries land in coverage-e2e/raw/ as JSONL;
// scripts/coverage-e2e.mjs converts them to istanbul reports. Every spec
// takes the `page` fixture (no spec opens its own context), so collecting
// here covers the whole suite; stopJSCoverage runs while the page is
// still open — a closed target refuses the call.
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
  page: async ({ page }, run, testInfo) => {
    if (e2eCoverage) {
      await page.coverage.startJSCoverage({ resetOnNavigation: false });
    }
    if (!shotsWanted) {
      page.screenshot = (() =>
        Promise.resolve(Buffer.alloc(0))) as typeof page.screenshot;
    }
    try {
      await run(page);
    } finally {
      if (e2eCoverage) {
        try {
          const entries = await page.coverage.stopJSCoverage();
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
        } catch (error) {
          console.warn(
            `e2e coverage: stopJSCoverage failed for ${testInfo.testId}: ${String(error)}`,
          );
        }
      }
    }
  },
});
