import { defineConfig } from '@playwright/test';

// README screenshots (`make readme-screens`). One backend in simulated
// mode with the readme seed (docs/images/readme/ fixtures only — no real
// account data), one Vite dev server proxying to it. Re-run the whole
// target for fresh images; it is deterministic via the frozen clock.
const apiPort = process.env.README_API_PORT ?? '8040';
const webPort = process.env.README_WEB_PORT ?? '5400';
const dataDir = '/tmp/stock-agent-readme-data';
const dbPath = '/tmp/stock-agent-readme.db';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'pr29-screens.e2e.ts',
  timeout: 180_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      // Fresh DB each run: the seeds write the run, a decision.v2 row, a
      // finished backtest and the readme fixture history, then the app
      // comes up paused on the frozen clock and runs its catch-up cycle.
      command: `cd ../backend && rm -f ${dbPath} ${dbPath}-wal ${dbPath}-shm && rm -rf ${dataDir} && mkdir -p ${dataDir} && export MODE=simulated SA_SIM_NOW=2026-10-15T17:00:00Z SA_DB_PATH=${dbPath} SA_DATA_DIR=${dataDir} SA_ENV_FILE=/tmp/no-such-env && ../.venv/bin/python -m tests.e2e_seed && ../.venv/bin/python -m tests.e2e_backtest_seed && ../.venv/bin/python -m tests.readme_seed && SA_HOST=127.0.0.1 SA_PORT=${apiPort} ../.venv/bin/python -m app.serve`,
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}`,
      env: { VITE_API_PORT: apiPort },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
