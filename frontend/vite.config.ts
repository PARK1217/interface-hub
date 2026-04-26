import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import vuetify from 'vite-plugin-vuetify';

// In docker-compose the backend is reachable as `http://backend:8000` from
// inside the frontend container. For local `npm run dev` outside docker,
// override with VITE_API_TARGET=http://localhost:8000.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://backend:8000';
const wsTarget = apiTarget.replace(/^http/, 'ws');

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['interface-hub.chobihome.site', 'localhost', '.chobihome.site'],
    allowedHosts: ['interface-hub.chobihome.site', 'localhost', '.chobihome.site'],
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/ws': { target: wsTarget, ws: true, changeOrigin: true },
    },
  },
});
