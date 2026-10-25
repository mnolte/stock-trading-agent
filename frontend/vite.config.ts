import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // The backend port comes from VITE_API_PORT (tests/scripts), else SA_PORT
  // in the repo-root .env — the same value the backend binds to — else 8000.
  const root = loadEnv(mode, resolve(__dirname, '..'), '');
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
  };
});
