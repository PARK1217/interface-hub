<template>
  <v-app>
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
          v-for="r in routes"
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
      <v-chip :color="connected ? 'success' : 'error'" variant="flat" size="small" class="mr-3">
        <v-icon start :icon="connected ? 'mdi-wifi' : 'mdi-wifi-off'" />
        {{ connected ? 'LIVE' : 'OFFLINE' }}
      </v-chip>
    </v-app-bar>

    <v-main>
      <v-container fluid class="pa-6">
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { subscribe } from '@/api/socket';

const route = useRoute();
const connected = ref(false);

const routes = [
  { path: '/dashboard', title: '대시보드', icon: 'mdi-view-dashboard' },
  { path: '/interfaces', title: '인터페이스', icon: 'mdi-api' },
  { path: '/logs', title: '호출 로그', icon: 'mdi-file-document-outline' },
  { path: '/incidents', title: '장애', icon: 'mdi-alert-circle-outline' },
  { path: '/performance', title: '성능 관리', icon: 'mdi-speedometer' },
  { path: '/sla', title: 'SLA', icon: 'mdi-chart-line' },
  { path: '/ai', title: 'AI 분석', icon: 'mdi-robot-outline' },
];

const currentTitle = computed(() => (route.meta?.title as string) ?? 'NOA Interface Hub');

onMounted(() => {
  subscribe(() => {
    connected.value = true;
  });
  // mark connected on first frame; socket re-establishes itself if dropped
  setTimeout(() => (connected.value = true), 500);
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