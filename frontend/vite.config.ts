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
  // 빌드/Dev 서버 시작 시점의 타임스탬프를 코드에 박아넣는다.
  // 정적 문서(예: 기획서 PDF) URL 뒤에 ?v=__BUILD_TS__ 형태로 붙여서
  // 매 배포마다 URL 이 바뀌도록 → 브라우저/PDF 뷰어 캐시 무효화.
  define: {
    __BUILD_TS__: JSON.stringify(Date.now().toString()),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['interface-hub.chobihome.site', 'localhost', '.chobihome.site'],
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/ws': { target: wsTarget, ws: true, changeOrigin: true },
    },
  },
});
