import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // The backend port comes from VITE_API_PORT (tests/scripts), else SA_PORT
  // in the repo-root .env — the same value the backend binds to — else 8000.
  const root = loadEnv(mode, resolve(import.meta.dirname, '..'), '');
  // E2E_COVERAGE=1 (CI only) keeps sourcemaps so the e2e V8 coverage can be
  // mapped back to src/**. Never set for the image build — maps must not
  // ship in dist/ there.
  const e2eCoverage = process.env.E2E_COVERAGE === '1';
  const apiPort = process.env.VITE_API_PORT ?? root.SA_PORT ?? '8000';
  const proxy = {
    '/api': `http://127.0.0.1:${apiPort}`,
    '/ws': {
      target: `http://127.0.0.1:${apiPort}`,
      ws: true,
    },
  };
  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy,
    },
    // E2E_PREVIEW=1 runs the e2e suite against the built bundle — one
    // build up front instead of per-module transforms on every page load.
    preview: { proxy },
    build: { sourcemap: e2eCoverage },
    test: {
      coverage: {
        provider: 'v8',
        reporter: ['text-summary', 'html', 'json-summary', 'json'],
        reportsDirectory: 'coverage',
        include: ['src/**/*.{ts,tsx}'],
        exclude: [
          'src/**/*.test.{ts,tsx}',
          'src/**/*.spec.{ts,tsx}',
          'src/main.tsx',
          'src/types/**',
          'src/**/*.d.ts',
        ],
      },
    },
  };
});
