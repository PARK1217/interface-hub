<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">성능 관리</h2>
      <v-spacer />
      <v-select
        v-model="days"
        :items="[1, 3, 7, 14, 30]"
        label="기간(일)"
        density="compact"
        hide-details
        style="max-width: 140px"
        @update:model-value="load"
      />
    </div>

    <!-- KPI strip -->
    <v-row class="mb-2">
      <v-col cols="12" md="3" v-for="kpi in kpis" :key="kpi.label">
        <v-card variant="elevated">
          <v-card-text>
            <div class="text-caption text-medium-emphasis">{{ kpi.label }}</div>
            <div class="text-h5 mt-1" :class="kpi.color">{{ kpi.value }}</div>
            <div class="text-caption mt-1 text-medium-emphasis">{{ kpi.hint }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row>
      <!-- Throughput chart -->
      <v-col cols="12" md="7">
        <v-card height="100%">
          <v-card-title class="text-subtitle-1">처리량 (TPS) · p95 응답시간 — 최근 24시간</v-card-title>
          <v-card-text>
            <apexchart v-if="tpsRows.length" type="line" height="320" :options="tpsOpts" :series="tpsSeries" />
            <div v-else class="text-medium-emphasis">데이터 없음</div>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- Slow top -->
      <v-col cols="12" md="5">
        <v-card height="100%">
          <v-card-title class="text-subtitle-1">가장 느린 호출 Top 10</v-card-title>
          <v-data-table
            :headers="slowHeaders"
            :items="slow"
            :loading="loading"
            density="compact"
            hide-default-footer
            :items-per-page="10"
          >
            <template #item.protocol="{ item }">
              <v-chip size="x-small" :color="protocolColor(item.protocol)">{{ item.protocol }}</v-chip>
            </template>
            <template #item.duration_ms="{ item }">
              <strong>{{ (item.duration_ms / 1000).toFixed(1) }}s</strong>
            </template>
            <template #item.called_at="{ item }">
              <span class="text-caption">{{ formatDateTime(item.called_at) }}</span>
            </template>
          </v-data-table>
        </v-card>
      </v-col>
    </v-row>

    <!-- Percentile table -->
    <v-card class="mt-4">
      <v-card-title class="text-subtitle-1">인터페이스별 응답시간 백분위 (정렬: p95 내림차순)</v-card-title>
      <v-data-table
        :headers="pHeaders"
        :items="rows"
        :loading="loading"
        density="comfortable"
        :items-per-page="20"
      >
        <template #item.protocol="{ item }">
          <v-chip size="x-small" :color="protocolColor(item.protocol)">{{ item.protocol }}</v-chip>
        </template>
        <template #item.interface_name="{ item }">
          <span>{{ item.interface_name }}</span>
          <v-chip
            v-if="item.deleted_at"
            size="x-small"
            color="grey"
            variant="tonal"
            prepend-icon="mdi-archive-outline"
            class="ml-2"
          >
            보관
          </v-chip>
        </template>
        <template #item.p50_ms="{ item }">{{ formatMs(item.p50_ms) }}</template>
        <template #item.p95_ms="{ item }">
          <span :class="latencyClass(item.p95_ms)">{{ formatMs(item.p95_ms) }}</span>
        </template>
        <template #item.p99_ms="{ item }">
          <span :class="latencyClass(item.p99_ms)">{{ formatMs(item.p99_ms) }}</span>
        </template>
        <template #item.max_ms="{ item }">{{ formatMs(item.max_ms) }}</template>
        <template #item.failure_rate="{ item }">
          <v-chip
            v-if="item.total_calls"
            size="small"
            :color="item.failure_count / item.total_calls > 0.05 ? 'error' : 'success'"
            variant="tonal"
          >
            {{ ((item.failure_count / item.total_calls) * 100).toFixed(2) }}%
          </v-chip>
        </template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Performance, type PercentileRow, type SlowCallRow, type ThroughputPoint } from '@/api/client';
import { formatDateTime } from '@/utils/format';

const days = ref(7);
const loading = ref(false);
const rows = ref<PercentileRow[]>([]);
const slow = ref<SlowCallRow[]>([]);
const tpsRows = ref<ThroughputPoint[]>([]);

const pHeaders = [
  { title: '인터페이스', key: 'interface_name' },
  { title: '프로토콜', key: 'protocol', width: 100 },
  { title: '기관', key: 'organization' },
  { title: '호출 수', key: 'total_calls', width: 90 },
  { title: '실패율', key: 'failure_rate', width: 100 },
  { title: 'p50', key: 'p50_ms', width: 90 },
  { title: 'p95', key: 'p95_ms', width: 90 },
  { title: 'p99', key: 'p99_ms', width: 90 },
  { title: 'max', key: 'max_ms', width: 90 },
];

const slowHeaders = [
  { title: '#', key: 'id', width: 70 },
  { title: '프로토콜', key: 'protocol', width: 90, sortable: false },
  { title: '인터페이스', key: 'interface_name' },
  { title: '소요', key: 'duration_ms', width: 80 },
  { title: '시각', key: 'called_at' },
];

const kpis = computed(() => {
  const total = rows.value.reduce((acc, r) => acc + r.total_calls, 0);
  const fail = rows.value.reduce((acc, r) => acc + r.failure_count, 0);
  const avgP95 = rows.value.length
    ? rows.value.reduce((acc, r) => acc + r.p95_ms, 0) / rows.value.length
    : 0;
  const slowest = rows.value.reduce((m, r) => (r.p95_ms > m ? r.p95_ms : m), 0);
  return [
    { label: '총 호출 (선택 기간)', value: total.toLocaleString(), hint: `${days.value}일`, color: 'text-primary' },
    { label: '평균 p95 응답', value: formatMs(avgP95), hint: '인터페이스 평균', color: latencyClass(avgP95) },
    { label: '최악 p95', value: formatMs(slowest), hint: '가장 느린 인터페이스', color: 'text-error' },
    { label: '실패 합계', value: fail.toLocaleString(), hint: total ? `${((fail / total) * 100).toFixed(2)}%` : '-', color: fail ? 'text-warning' : 'text-success' },
  ];
});

const tpsSeries = computed(() => [
  { name: 'TPS', type: 'column', data: tpsRows.value.map((p) => [new Date(p.bucket).getTime(), p.tps]) },
  { name: 'p95 (ms)', type: 'line', data: tpsRows.value.map((p) => [new Date(p.bucket).getTime(), Math.round(p.p95_ms)]) },
]);

const tpsOpts = {
  chart: { id: 'tps-chart', toolbar: { show: false } },
  stroke: { width: [0, 3] },
  dataLabels: { enabled: false },
  xaxis: { type: 'datetime' },
  yaxis: [
    { title: { text: 'TPS' } },
    { opposite: true, title: { text: 'ms' } },
  ],
  colors: ['#1F3A93', '#E04F5F'],
};

function protocolColor(p: string) {
  return ({ REST: 'primary', SOAP: 'secondary', FTP: 'warning', MQ: 'success', BATCH: 'purple' } as Record<string, string>)[p] ?? 'grey';
}

function formatMs(v: number): string {
  if (!v) return '-';
  if (v >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${Math.round(v)} ms`;
}

function latencyClass(v: number): string {
  if (v <= 300) return 'text-success';
  if (v <= 1500) return 'text-info';
  if (v <= 5000) return 'text-warning';
  return 'text-error';
}

async function load() {
  loading.value = true;
  try {
    const [p, s, t] = await Promise.all([
      Performance.percentiles(days.value),
      Performance.slowTop(10, days.value),
      Performance.throughput(15, 24),
    ]);
    rows.value = p.data;
    slow.value = s.data;
    tpsRows.value = t.data;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>