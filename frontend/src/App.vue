<template>
  <!-- 로그인 화면은 자체 레이아웃 (Login.vue 가 v-app 직접 렌더) -->
  <router-view v-if="$route.path === '/login'" />

  <v-app v-else>
    <v-navigation-drawer permanent color="primary" theme="dark" class="no-print">
      <v-list-item class="pa-4">
        <template #prepend>
          <v-icon icon="mdi-hub" size="32" />
        </template>
        <v-list-item-title class="text-h6">NOA Hub</v-list-item-title>
        <v-list-item-subtitle>Interface Control</v-list-item-subtitle>
      </v-list-item>
      <v-divider />
      <v-list density="comfortable" nav>
        <v-list-item
          v-for="r in visibleRoutes"
          :key="r.path"
          :to="r.path"
          :prepend-icon="r.icon"
          :title="r.title"
        />
      </v-list>
    </v-navigation-drawer>

    <v-app-bar elevation="1" class="no-print">
      <v-app-bar-title>{{ currentTitle }}</v-app-bar-title>
      <v-spacer />
      <!-- Phase B.7 — 미해결 incident 카운트 뱃지. 클릭 시 incidents 페이지로. -->
      <v-btn
        variant="text"
        class="mr-2"
        :title="openIncidentCount > 0 ? `미해결 장애 ${openIncidentCount}건 — 클릭해 이동` : '미해결 장애 없음'"
        @click="router.push('/incidents')"
      >
        <v-badge
          :content="openIncidentCount > 99 ? '99+' : openIncidentCount"
          :model-value="openIncidentCount > 0"
          color="error"
          offset-x="-2"
          offset-y="2"
        >
          <v-icon :icon="openIncidentCount > 0 ? 'mdi-bell-ring-outline' : 'mdi-bell-outline'" />
        </v-badge>
      </v-btn>
      <v-chip :color="connected ? 'success' : 'error'" variant="flat" size="small" class="mr-3">
        <v-icon start :icon="connected ? 'mdi-wifi' : 'mdi-wifi-off'" />
        {{ connected ? 'LIVE' : 'OFFLINE' }}
      </v-chip>
      <v-menu v-if="auth.user" offset="6">
        <template #activator="{ props }">
          <v-btn v-bind="props" variant="text" class="mr-2">
            <v-icon start icon="mdi-account-circle-outline" />
            {{ auth.user.full_name || auth.user.username }}
            <v-chip class="ml-2" size="x-small" :color="roleColor">
              {{ auth.user.role }}
            </v-chip>
          </v-btn>
        </template>
        <v-list density="compact">
          <v-list-item disabled>
            <v-list-item-title class="text-caption">{{ auth.user.username }}</v-list-item-title>
            <v-list-item-subtitle class="text-caption">
              마지막 로그인: {{ formatDateTimeShort(auth.user.last_login_at) || '-' }}
            </v-list-item-subtitle>
          </v-list-item>
          <v-divider />
          <v-list-item prepend-icon="mdi-lock-reset" @click="pwDialog = true">
            <v-list-item-title>비밀번호 변경</v-list-item-title>
          </v-list-item>
          <v-list-item prepend-icon="mdi-logout" @click="onLogout">
            <v-list-item-title>로그아웃</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
    </v-app-bar>

    <v-main>
      <v-container fluid class="pa-6">
        <router-view />
      </v-container>
    </v-main>

    <!--
      Phase B.4 강제 비밀번호 변경 모달.
      auth.mustChangePassword=true 면 forced 모드로 자동 노출 + 닫기 차단.
      AppBar 메뉴의 "비밀번호 변경" 클릭 시에는 일반 모드로 노출.
    -->
    <ChangePasswordDialog
      v-model="pwDialog"
      :forced="auth.mustChangePassword"
      @changed="onPasswordChanged"
    />

    <!--
      Phase B.7 알림 toast — 새 incident WS 수신 시 (should_alert=true 만) 노출.
      음소거된 인터페이스/채널은 백엔드에서 should_alert=false 로 내려옴 → 무시.
      여러 건 동시 발생 시 대기열에 쌓고 순차 표시 (Vuetify v-snackbar 단일 인스턴스 한계).
    -->
    <v-snackbar
      v-model="toastShow"
      :timeout="6000"
      :color="toastSeverity === 'critical' ? 'error' : 'warning'"
      location="top right"
      multi-line
    >
      <div class="d-flex align-start">
        <v-icon
          :icon="toastSeverity === 'critical' ? 'mdi-alert-octagon' : 'mdi-alert'"
          class="mr-2 mt-1"
        />
        <div>
          <div class="text-subtitle-2">
            {{ toastTitle }}
          </div>
          <div class="text-caption">{{ toastBody }}</div>
        </div>
      </div>
      <template #actions>
        <v-btn variant="text" @click="onToastClick">보기</v-btn>
      </template>
    </v-snackbar>
  </v-app>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { subscribe } from '@/api/socket';
import { Incidents } from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import { formatDateTimeShort } from '@/utils/format';
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const connected = ref(false);
const pwDialog = ref(false);

// Phase B.7 — 미해결 incident 카운트 + toast 큐 ----------------------------
const openIncidentCount = ref(0);
const toastShow = ref(false);
const toastTitle = ref('');
const toastBody = ref('');
const toastSeverity = ref<'warning' | 'critical'>('warning');
const toastIncidentId = ref<number | null>(null);
const toastQueue = ref<{ title: string; body: string; severity: 'warning' | 'critical'; id: number }[]>([]);

let countTimer: ReturnType<typeof setInterval> | null = null;
let unsubscribeWs: (() => void) | null = null;

async function refreshIncidentCount() {
  if (!auth.isAuthenticated) {
    openIncidentCount.value = 0;
    return;
  }
  try {
    // 미해결만 — 백엔드는 unresolved_only 파라미터 사용
    const res = await Incidents.list({ unresolved_only: true });
    openIncidentCount.value = res.data.length;
  } catch {
    /* 401 등은 axios 인터셉터가 처리 */
  }
}

function showNextToast() {
  if (toastShow.value) return;
  const next = toastQueue.value.shift();
  if (!next) return;
  toastTitle.value = next.title;
  toastBody.value = next.body;
  toastSeverity.value = next.severity;
  toastIncidentId.value = next.id;
  toastShow.value = true;
}

watch(toastShow, (v) => {
  if (!v) {
    // 닫히면 다음 큐 아이템 표시 (살짝 딜레이)
    setTimeout(showNextToast, 300);
  }
});

function onToastClick() {
  toastShow.value = false;
  router.push('/incidents');
}

// Phase B.4 — 강제 비밀번호 변경이 필요하면 다이얼로그 자동 노출.
// 로그인 직후 / 페이지 진입 시 모두 동작하도록 watch + onMounted 둘 다.
watch(
  () => auth.mustChangePassword,
  (need) => {
    if (need) pwDialog.value = true;
  },
  { immediate: true },
);

function onPasswordChanged() {
  // 강제 변경 완료 → 다이얼로그 닫고 일반 사용 가능 (auth.user.must_change_password = false)
  pwDialog.value = false;
}

const routes = [
  { path: '/dashboard', title: '대시보드', icon: 'mdi-view-dashboard' },
  { path: '/interfaces', title: '인터페이스', icon: 'mdi-api' },
  { path: '/logs', title: '호출 로그', icon: 'mdi-file-document-outline' },
  { path: '/incidents', title: '장애', icon: 'mdi-alert-circle-outline' },
  { path: '/performance', title: '성능 관리', icon: 'mdi-speedometer' },
  { path: '/sla', title: 'SLA', icon: 'mdi-chart-line' },
  { path: '/ai', title: 'AI 분석', icon: 'mdi-robot-outline' },
  // 감사 로그는 OPERATOR 에게는 메뉴 숨김 (감사관/관리자만)
  { path: '/audit-logs', title: '감사 로그', icon: 'mdi-shield-search', auditorOnly: true },
  // 알림 룰은 ADMIN 만 (전역 정책)
  { path: '/alert-rules', title: '알림 룰', icon: 'mdi-bell-cog-outline', adminOnly: true },
  // 사용자 관리는 ADMIN 만
  { path: '/users', title: '사용자 관리', icon: 'mdi-account-group-outline', adminOnly: true },
];

const visibleRoutes = computed(() =>
  routes.filter((r) => {
    if (r.adminOnly && auth.role !== 'ADMIN') return false;
    if (r.auditorOnly && !(auth.role === 'ADMIN' || auth.role === 'VIEWER')) return false;
    return true;
  }),
);

const currentTitle = computed(() => (route.meta?.title as string) ?? 'NOA Interface Hub');

const roleColor = computed(() => {
  switch (auth.user?.role) {
    case 'ADMIN': return 'error';
    case 'OPERATOR': return 'warning';
    case 'VIEWER': return 'info';
    default: return 'grey';
  }
});

async function onLogout() {
  await auth.logout();
  router.push('/login');
}

onMounted(async () => {
  unsubscribeWs = subscribe((channel, data) => {
    connected.value = true;
    // Phase B.7 — incident 이벤트는 카운트 갱신 + toast 큐잉
    if (channel === 'incident' && data) {
      // 새 장애가 들어오면 카운트 +1 (정확도 위해 곧이어 서버 카운트 동기화도 트리거)
      openIncidentCount.value += 1;
      // should_alert=true 인 경우만 toast (음소거/채널OFF 시 백엔드가 false 로 내림)
      if (data.should_alert) {
        toastQueue.value.push({
          id: data.id,
          title: `[${(data.severity || 'warning').toUpperCase()}] ${data.interface_name}`,
          body: `${data.type}${data.summary ? ' · ' + data.summary : ''}`,
          severity: data.severity === 'critical' ? 'critical' : 'warning',
        });
        showNextToast();
      }
      // 동기화 — 다중 탭/해결처리 등으로 카운트가 어긋나는 케이스 보정
      refreshIncidentCount();
    }
    // 장애 해결 이벤트가 따로 있다면 여기서 카운트 -1 도 가능 (현재는 단순 폴링으로 보정)
  });
  setTimeout(() => (connected.value = true), 500);
  // localStorage 의 사용자 정보가 stale 일 수 있어 (다른 탭에서 비번 변경 등) 한 번 동기화
  // — refreshMe 가 401 이면 자동 로그아웃 처리됨
  if (auth.isAuthenticated) await auth.refreshMe();

  // 미해결 incident 카운트 — 초기 로드 + 30초 주기 폴링 (WS 못 받은 케이스 보정)
  await refreshIncidentCount();
  countTimer = setInterval(refreshIncidentCount, 30000);
});

onBeforeUnmount(() => {
  if (countTimer) clearInterval(countTimer);
  if (unsubscribeWs) unsubscribeWs();
});
</script>

<style>
@media print {
  /* Reset page so charts & tables get the full A4 landscape printable area.
     Without this Vuetify keeps reserving 256px on the left for the (now
     hidden) navigation drawer, which causes graphs to overflow / get clipped. */
  @page {
    size: A4 landscape;
    margin: 8mm;
  }

  /* Hide app chrome */
  .no-print,
  .v-navigation-drawer,
  .v-app-bar {
    display: none !important;
  }

  /* Vuetify's v-main applies a fixed left padding to clear the drawer.
     Cancel that so the report uses the full page width. */
  .v-main {
    padding: 0 !important;
    margin: 0 !important;
  }
  .v-container,
  .v-container.pa-6 {
    padding: 0 !important;
    max-width: 100% !important;
  }
  .v-application,
  .v-application__wrap {
    background: #fff !important;
  }

  /* ApexCharts SVG must shrink to the new printable width */
  .apexcharts-canvas,
  .apexcharts-canvas svg,
  .apexcharts-svg {
    width: 100% !important;
    max-width: 100% !important;
    height: auto !important;
  }

  /* Hide pagination controls inside data tables when printing */
  .v-data-table-footer,
  .v-data-table__th__sort-badge,
  .v-data-table-rows-no-data {
    display: none !important;
  }

  /* Avoid table rows being cut between pages */
  tr,
  .v-card {
    page-break-inside: avoid;
  }
}
</style>