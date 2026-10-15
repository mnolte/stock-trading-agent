import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': `http://127.0.0.1:${process.env.VITE_API_PORT ?? 8000}`,
      '/ws': `http://127.0.0.1:${process.env.VITE_API_PORT ?? 8000}`,
    },
  },
});
