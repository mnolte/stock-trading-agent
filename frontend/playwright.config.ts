import { defineConfig } from '@playwright/test';

const apiPort = process.env.E2E_API_PORT ?? '8020';
const webPort = process.env.E2E_WEB_PORT ?? '5300';

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
      command: `cd ../backend && MODE=simulated SA_SIM_NOW=2026-10-15T17:00:00Z SA_DB_PATH=/tmp/stock-agent-e2e.db ../.venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port ${apiPort}`,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}`,
      env: { VITE_API_PORT: apiPort },
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
});
