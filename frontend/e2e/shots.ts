import os from 'node:os';
import path from 'node:path';

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
});
