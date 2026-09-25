/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The dashboard calls the API under /api/, on its own origin (ADR 016). In development
// and in `vite preview`, this server relays /api/… to the API and drops the prefix.
const relayToApi = {
  '/api/': {
    target: 'http://127.0.0.1:8000',
    rewrite: (path: string) => path.replace(/^\/api(?=\/)/, ''),
  },
};

export default defineConfig({
  plugins: [react()],
  // Loopback only: nothing is exposed to the network during development.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy: relayToApi },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy: relayToApi },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
