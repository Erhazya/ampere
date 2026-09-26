/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The dashboard calls the API under /api/, on its own origin (ADR 016). The API declares its
// routes under /api itself (ADR 018), so this server relays /api/… to it unchanged.
const relayToApi = { '/api/': 'http://127.0.0.1:8000' };

export default defineConfig({
  plugins: [react()],
  // Loopback only: nothing is exposed to the network during development. No CORS either,
  // since the dashboard and the API share one origin (ADR 016). `vite preview` reuses these
  // settings, on its own port, 4173.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, cors: false, proxy: relayToApi },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
