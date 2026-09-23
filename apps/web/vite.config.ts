import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// `pnpm dev` proxies the API to a running `graphene-twin serve` (default port 8080).
const api = process.env.TWIN_API ?? 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': api } },
  build: { chunkSizeWarningLimit: 1500 },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
