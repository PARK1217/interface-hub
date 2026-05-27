/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<object, object, any>;
  export default component;
}

declare module 'vue3-apexcharts';

// vite.config.ts 의 define 으로 주입되는 빌드 타임스탬프 (cache-buster 용)
declare const __BUILD_TS__: string;