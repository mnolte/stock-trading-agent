import { defineConfig } from '@playwright/test';

const apiPort = process.env.E2E_API_PORT ?? '8020';
const webPort = process.env.E2E_WEB_PORT ?? '5300';
// E2E_PREVIEW=1 serves the built bundle (vite preview) instead of the dev
// server: one `pnpm build` up front, then fast static page loads. CI sets
// it; locally the dev server stays for reuseExistingServer + HMR.
const preview = process.env.E2E_PREVIEW === '1';
// E2E_AUTH=1 runs the backend with SA_AUTH=required and seeds the single
// account (tests/e2e_auth_seed.py) so the PR 23 sign-in spec drives the
// real flow. Other specs run in a separate invocation without it.
const e2eAuth = process.env.E2E_AUTH === '1';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
  },
  webServer: [
    {
      // tests/e2e_seed.py starts the e2e run on Monday 5 Oct so a weekly
      // review is due (the clock is frozen on Thursday 15 Oct).
      // tests/e2e_backtest_seed.py adds a finished backtest run so the
      // Results panels have data (the frozen clock can't run one).
      // rm -f clears a leftover run.lock from the cached data dir (or a
      // killed local run) so the app neither refuses e2e startup on a
      // recycled pid nor raises the unclean-run banner.
      command: `cd ../backend && export MODE=simulated SA_SIM_NOW=2026-10-15T17:00:00Z SA_DB_PATH=/tmp/stock-agent-e2e.db SA_DATA_DIR=/tmp/stock-agent-e2e-data ${e2eAuth ? 'SA_AUTH=required' : ''} && rm -f "$SA_DATA_DIR/run.lock" && ../.venv/bin/python -m tests.e2e_seed && ../.venv/bin/python -m tests.e2e_backtest_seed && ${e2eAuth ? '../.venv/bin/python -m tests.e2e_auth_seed && ' : ''}SA_HOST=127.0.0.1 SA_PORT=${apiPort} ../.venv/bin/python -m app.serve`,
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: preview
        ? `pnpm build && pnpm preview --host 127.0.0.1 --port ${webPort} --strictPort`
        : `pnpm dev --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}`,
      env: { VITE_API_PORT: apiPort },
      reuseExistingServer: true,
      timeout: preview ? 240_000 : 60_000,
    },
  ],
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
});
