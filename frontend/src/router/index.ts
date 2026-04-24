import { createRouter, createWebHistory } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes = [
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true, title: '로그인' } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '대시보드' } },
  { path: '/topology', component: () => import('@/views/Topology.vue'), meta: { title: '토폴로지' } },
  { path: '/interfaces', component: () => import('@/views/Interfaces.vue'), meta: { title: '인터페이스' } },
  { path: '/logs', component: () => import('@/views/Logs.vue'), meta: { title: '호출 로그' } },
  { path: '/incidents', component: () => import('@/views/Incidents.vue'), meta: { title: '장애' } },
  { path: '/performance', component: () => import('@/views/Performance.vue'), meta: { title: '성능 관리' } },
  { path: '/retry-analytics', component: () => import('@/views/RetryAnalytics.vue'), meta: { title: '자동 복구 분석' } },
  { path: '/sla', component: () => import('@/views/Sla.vue'), meta: { title: 'SLA' } },
  { path: '/ai', component: () => import('@/views/AiAssistant.vue'), meta: { title: 'AI 분석' } },
  {
    path: '/alert-rules',
    component: () => import('@/views/AlertRules.vue'),
    meta: { title: '알림 룰', adminOnly: true },
  },
  {
    path: '/audit-logs',
    component: () => import('@/views/AuditLogs.vue'),
    meta: { title: '감사 로그', auditorOnly: true },
  },
  {
    path: '/users',
    component: () => import('@/views/Users.vue'),
    meta: { title: '사용자 관리', adminOnly: true },
  },
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
  // 감사관 전용 라우트 (감사 로그 등) — OPERATOR 는 차단
  if (to.meta?.auditorOnly && auth.role === 'OPERATOR') {
    return { path: '/dashboard' };
  }
  // ADMIN 전용 (사용자 관리 등) — OPERATOR/VIEWER 차단
  if (to.meta?.adminOnly && auth.role !== 'ADMIN') {
    return { path: '/dashboard' };
  }
  return true;
});

export default router;