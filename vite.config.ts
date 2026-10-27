import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Same origin in dev so the media proxy and API need no CORS setup.
      '/api': process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000',
    },
  },
});
