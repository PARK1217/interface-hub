import { createRouter, createWebHistory } from 'vue-router';

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '대시보드' } },
  { path: '/interfaces', component: () => import('@/views/Interfaces.vue'), meta: { title: '인터페이스' } },
  { path: '/logs', component: () => import('@/views/Logs.vue'), meta: { title: '호출 로그' } },
  { path: '/incidents', component: () => import('@/views/Incidents.vue'), meta: { title: '장애' } },
  { path: '/performance', component: () => import('@/views/Performance.vue'), meta: { title: '성능 관리' } },
  { path: '/sla', component: () => import('@/views/Sla.vue'), meta: { title: 'SLA' } },
  { path: '/ai', component: () => import('@/views/AiAssistant.vue'), meta: { title: 'AI 분석' } },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});