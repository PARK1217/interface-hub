<template>
  <div>
    <v-row>
      <v-col cols="12" md="3" v-for="kpi in kpis" :key="kpi.label">
        <v-card variant="elevated">
          <v-card-text>
            <div class="text-caption text-medium-emphasis">{{ kpi.label }}</div>
            <div class="text-h4 mt-1" :class="kpi.color">{{ kpi.value }}</div>
            <div class="text-caption mt-1">{{ kpi.hint }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-2">
      <v-col cols="12" md="8">
        <v-card>
          <v-card-title class="text-subtitle-1">호출량 / 응답시간 (최근 6시간)</v-card-title>
          <v-card-text>
            <apexchart type="line" height="320" :options="chartOpts" :series="chartSeries" />
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="4">
        <v-card>
          <v-card-title class="text-subtitle-1">실시간 호출 (LIVE)</v-card-title>
          <v-list density="compact" max-height="320" class="overflow-y-auto">
            <v-list-item v-for="c in live.recentCalls" :key="c.id">
              <template #prepend>
                <v-icon
                  :color="c.status === 'SUCCESS' ? 'success' : 'error'"
                  :icon="c.status === 'SUCCESS' ? 'mdi-check-circle' : 'mdi-alert-circle'"
                />
              </template>
              <v-list-item-title>
                #{{ c.interface_id }} · {{ c.status }} · {{ c.duration_ms }}ms
              </v-list-item-title>
              <v-list-item-subtitle>{{ c.called_at }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item v-if="!live.recentCalls.length">
              <v-list-item-subtitle>아직 수신된 호출이 없습니다.</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { CallLogs } from '@/api/client';
import { useLiveStore } from '@/stores/live';

const live = useLiveStore();
live.bind();

const stats = ref({ total: 0, success: 0, failure: 0, avg_duration_ms: 0, success_rate: 0 });
const series = ref<{ bucket: string; total: number; success: number; failure: number; avg_duration_ms: number }[]>([]);

const kpis = computed(() => [
  { label: '총 호출 (24h)', value: stats.value.total, hint: '전체 인터페이스 합산', color: 'text-primary' },
  {
    label: '성공률',
    value: `${(stats.value.success_rate * 100).toFixed(1)}%`,
    hint: `성공 ${stats.value.success} / 실패 ${stats.value.failure}`,
    color: stats.value.success_rate >= 0.95 ? 'text-success' : 'text-warning',
  },
  {
    label: '평균 응답시간',
    value: `${stats.value.avg_duration_ms.toFixed(0)} ms`,
    hint: '최근 24시간 평균',
    color: 'text-info',
  },
  {
    label: '실시간 장애',
    value: live.recentIncidents.length,
    hint: '세션 동안 수신',
    color: live.recentIncidents.length ? 'text-error' : 'text-success',
  },
]);

const chartSeries = computed(() => [
  { name: '호출 수', type: 'column', data: series.value.map((p) => [new Date(p.bucket).getTime(), p.total]) },
  { name: '평균 응답(ms)', type: 'line', data: series.value.map((p) => [new Date(p.bucket).getTime(), Math.round(p.avg_duration_ms)]) },
]);

const chartOpts = {
  chart: { id: 'live-chart', toolbar: { show: false } },
  stroke: { width: [0, 3] },
  dataLabels: { enabled: false },
  xaxis: { type: 'datetime' },
  yaxis: [
    { title: { text: '호출 수' } },
    { opposite: true, title: { text: 'ms' } },
  ],
  colors: ['#1F3A93', '#F6A623'],
};

async function load() {
  const [s, t] = await Promise.all([CallLogs.stats(), CallLogs.timeseries({ bucket_minutes: 5 })]);
  stats.value = s.data;
  series.value = t.data;
}

onMounted(load);
</script>