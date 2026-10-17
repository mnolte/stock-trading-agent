import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // The backend port comes from VITE_API_PORT (tests/scripts), else SA_PORT
  // in the repo-root .env — the same value the backend binds to — else 8000.
  const root = loadEnv(mode, resolve(__dirname, '..'), '');
  const apiPort = process.env.VITE_API_PORT ?? root.SA_PORT ?? '8000';
  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': `http://127.0.0.1:${apiPort}`,
        '/ws': {
          target: `http://127.0.0.1:${apiPort}`,
          ws: true,
        },
      },
    },
  };
});
