import os from 'node:os';
import path from 'node:path';

/** Where a spec writes its screenshots. By default a temp folder, so a
 * normal `make test` can never overwrite an earlier PR's screenshots; pass
 * SCREENSHOT_DIR (e.g. `SCREENSHOT_DIR=../docs/screenshots/pr-14`) to write
 * a PR's screenshots into docs/screenshots/pr-<n>/ on purpose. */
export function screenshotDir(pr: string): string {
  const explicit = process.env.SCREENSHOT_DIR;
  if (explicit) return path.resolve(explicit);
  return path.join(os.tmpdir(), 'stock-agent-e2e-screenshots', pr);
}
