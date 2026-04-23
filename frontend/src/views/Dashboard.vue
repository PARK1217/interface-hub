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
              <v-list-item-subtitle>{{ formatDateTimeShort(c.called_at) }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item v-if="!live.recentCalls.length">
              <v-list-item-subtitle>아직 수신된 호출이 없습니다.</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-2">
      <v-col cols="12">
        <v-card>
          <v-card-title class="d-flex align-center">
            <span class="text-subtitle-1">시간대 × 인터페이스 호출 히트맵 (최근 7일)</span>
            <v-spacer />
            <v-btn-toggle v-model="heatMode" density="compact" mandatory color="primary">
              <v-btn value="count" size="small">호출량</v-btn>
              <v-btn value="failure" size="small">실패율</v-btn>
            </v-btn-toggle>
          </v-card-title>
          <v-card-text>
            <apexchart
              v-if="heatSeries.length"
              type="heatmap"
              :height="40 + 28 * heatSeries.length"
              :options="heatOpts"
              :series="heatSeries"
            />
            <div v-else class="text-medium-emphasis">데이터 없음</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { CallLogs, type HeatmapRow } from '@/api/client';
import { useLiveStore } from '@/stores/live';
import { formatDateTimeShort } from '@/utils/format';

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

const heatMode = ref<'count' | 'failure'>('count');
const heatRows = ref<HeatmapRow[]>([]);

const heatSeries = computed(() =>
  heatRows.value.map((r) => ({
    name: `#${r.interface_id} ${r.interface_name}${r.deleted_at ? ' (보관)' : ''}`,
    data: r.cells.map((c) => ({
      x: String(c.hour).padStart(2, '0'),
      y: heatMode.value === 'count' ? c.count : Math.round(c.failure_rate * 1000) / 10,
    })),
  })),
);

const heatOpts = computed(() => ({
  chart: { id: 'heatmap', toolbar: { show: false } },
  dataLabels: { enabled: false },
  xaxis: { type: 'category', title: { text: '시각 (시, KST)' } },
  plotOptions: {
    heatmap: {
      shadeIntensity: 0.5,
      colorScale:
        heatMode.value === 'count'
          ? {
              ranges: [
                { from: 0, to: 0, color: '#eef2f7', name: '0' },
                { from: 1, to: 20, color: '#cfd8dc', name: '~20' },
                { from: 21, to: 100, color: '#90caf9', name: '~100' },
                { from: 101, to: 300, color: '#1976d2', name: '~300' },
                { from: 301, to: 99999, color: '#0d47a1', name: '300+' },
              ],
            }
          : {
              ranges: [
                { from: 0, to: 0, color: '#e8f5e9', name: '0%' },
                { from: 0.01, to: 1, color: '#aed581', name: '~1%' },
                { from: 1.01, to: 5, color: '#ffeb3b', name: '~5%' },
                { from: 5.01, to: 15, color: '#fb8c00', name: '~15%' },
                { from: 15.01, to: 100, color: '#e53935', name: '15%+' },
              ],
            },
    },
  },
  tooltip: {
    y: {
      formatter: (val: number) =>
        heatMode.value === 'count' ? `${val} 호출` : `${val.toFixed(1)}%`,
    },
  },
}));

async function load() {
  const [s, t, h] = await Promise.all([
    CallLogs.stats(),
    CallLogs.timeseries({ bucket_minutes: 5 }),
    CallLogs.heatmap({ days: 7 }),
  ]);
  stats.value = s.data;
  series.value = t.data;
  heatRows.value = h.data;
}

onMounted(load);
</script>