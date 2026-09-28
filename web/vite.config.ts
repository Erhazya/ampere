/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The dashboard calls the API under /api/, on its own origin (ADR 016). The API declares its
// routes under /api itself (ADR 018), so this server relays /api/… to it unchanged, Host header
// included. Hence the object form: the string shorthand would turn on changeOrigin, and the
// redirects of the API would then point at port 8000.
const relayToApi = { '/api/': { target: 'http://127.0.0.1:8000' } };

export default defineConfig({
  plugins: [react()],
  // Loopback only: nothing is exposed to the network during development. No CORS either,
  // since the dashboard and the API share one origin (ADR 016). `vite preview` reuses these
  // settings, on its own port, 4173.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, cors: false, proxy: relayToApi },
  // ECharts, even module by module, weighs about 500 kB (168 kB compressed) in a file of its own,
  // which only the Data screen loads (ADR 010 and 029): the limit leaves it room to grow a little.
  build: { chunkSizeWarningLimit: 600 },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
