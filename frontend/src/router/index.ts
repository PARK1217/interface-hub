import { createRouter, createWebHistory } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes = [
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true, title: '로그인' } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '대시보드' } },
  { path: '/interfaces', component: () => import('@/views/Interfaces.vue'), meta: { title: '인터페이스' } },
  { path: '/logs', component: () => import('@/views/Logs.vue'), meta: { title: '호출 로그' } },
  { path: '/incidents', component: () => import('@/views/Incidents.vue'), meta: { title: '장애' } },
  { path: '/performance', component: () => import('@/views/Performance.vue'), meta: { title: '성능 관리' } },
  { path: '/sla', component: () => import('@/views/Sla.vue'), meta: { title: 'SLA' } },
  { path: '/ai', component: () => import('@/views/AiAssistant.vue'), meta: { title: 'AI 분석' } },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 인증 가드 — public 라우트가 아닌데 토큰 없으면 /login 으로
router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.meta?.public) return true;
  if (!auth.isAuthenticated) {
    return { path: '/login', query: { next: to.fullPath } };
  }
  return true;
});

export default router;