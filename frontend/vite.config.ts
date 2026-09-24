import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ['/tasks', '/health', '/docs', '/openapi.json', '/demo'].map(path => [
        path, { target: process.env.CLIPFORGE_API_URL || 'http://127.0.0.1:8200' },
      ]),
    ),
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: false },
});
