<template>
  <div>
    <v-row>
      <v-col cols="12" md="3" v-for="kpi in kpis" :key="kpi.label">
        <v-card variant="elevated" height="100%">
          <v-card-text>
            <div class="text-caption text-medium-emphasis">{{ kpi.label }}</div>
            <div class="text-h4 mt-1 kpi-value" :class="kpi.color">{{ kpi.value }}</div>
            <div class="text-caption mt-1">{{ kpi.hint }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-2">
      <v-col cols="12" md="8">
        <v-card>
          <v-card-title class="text-subtitle-1">호출량 / 응답시간</v-card-title>
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
            <!-- 커스텀 범례 — 호버 시 강조, 클릭 시 해당 구간만 컬러로 표시 (필터). 다시 클릭하면 해제. -->
            <div class="heatmap-legend mb-3" v-if="heatSeries.length">
              <span
                v-for="(r, idx) in heatRanges"
                :key="r.name"
                class="legend-chip"
                :class="{ active: activeRangeIdx === idx, dimmed: activeRangeIdx !== null && activeRangeIdx !== idx }"
                :title="activeRangeIdx === idx ? '클릭해 필터 해제' : `${r.name} 만 강조 (다시 클릭하면 해제)`"
                @click="toggleRange(idx)"
              >
                <span class="legend-swatch" :style="{ background: r.color }" />
                {{ r.name }}
              </span>
              <v-btn
                v-if="activeRangeIdx !== null"
                size="x-small"
                variant="text"
                prepend-icon="mdi-close"
                class="ml-2"
                @click="activeRangeIdx = null"
              >
                필터 해제
              </v-btn>
            </div>
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
import { computed, onMounted, ref, watch } from 'vue';
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

// Y축 최소 눈금 — 호출 수가 작을 때(예: 전부 1) 막대가 100% 로 차서 시각화 의미가 사라지는 것 방지.
// 실제 max 가 5 미만이면 5 를 상한으로 강제해 "아 호출이 적구나" 여백 시각화.
const chartOpts = computed(() => {
  const maxCount = Math.max(1, ...series.value.map((p) => p.total));
  return {
    chart: { id: 'live-chart', toolbar: { show: false } },
    stroke: { width: [0, 3] },
    dataLabels: { enabled: false },
    xaxis: { type: 'datetime' },
    yaxis: [
      { title: { text: '호출 수' }, min: 0, max: Math.max(5, maxCount), forceNiceScale: true },
      { opposite: true, title: { text: 'ms' }, min: 0 },
    ],
    colors: ['#1F3A93', '#F6A623'],
  };
});

const heatMode = ref<'count' | 'failure'>('count');
const heatRows = ref<HeatmapRow[]>([]);
// 범례 클릭으로 특정 구간만 강조하는 필터 상태. null=전체 표시, 숫자=해당 idx 만 컬러.
const activeRangeIdx = ref<number | null>(null);

interface HeatRange { from: number; to: number; color: string; name: string }

// 색 팔레트는 회색 단계를 줄이고 청·적 계열로 대비 강화 (이전엔 ~20 회색이 너무 흐릿).
const COUNT_RANGES: HeatRange[] = [
  { from: 0,   to: 0,     color: '#eceff1', name: '0' },
  { from: 1,   to: 20,    color: '#80deea', name: '~20' },
  { from: 21,  to: 100,   color: '#26c6da', name: '~100' },
  { from: 101, to: 300,   color: '#1976d2', name: '~300' },
  { from: 301, to: 99999, color: '#0d47a1', name: '300+' },
];
const FAILURE_RANGES: HeatRange[] = [
  { from: 0,     to: 0,   color: '#c8e6c9', name: '0%' },
  { from: 0.01,  to: 1,   color: '#aed581', name: '~1%' },
  { from: 1.01,  to: 5,   color: '#ffd54f', name: '~5%' },
  { from: 5.01,  to: 15,  color: '#fb8c00', name: '~15%' },
  { from: 15.01, to: 100, color: '#c62828', name: '15%+' },
];

const heatRanges = computed<HeatRange[]>(() =>
  heatMode.value === 'count' ? COUNT_RANGES : FAILURE_RANGES,
);

// 모드 바뀌면 필터 자동 해제 (범위 정의가 달라지므로 idx 가 의미 없어짐)
watch(heatMode, () => { activeRangeIdx.value = null; });

function toggleRange(idx: number) {
  activeRangeIdx.value = activeRangeIdx.value === idx ? null : idx;
}

const heatSeries = computed(() =>
  heatRows.value.map((r) => ({
    name: `#${r.interface_id} ${r.interface_name}${r.deleted_at ? ' (보관)' : ''}`,
    data: r.cells.map((c) => ({
      x: String(c.hour).padStart(2, '0'),
      y: heatMode.value === 'count' ? c.count : Math.round(c.failure_rate * 1000) / 10,
    })),
  })),
);

const heatOpts = computed(() => {
  // 활성 범위가 있으면 그 idx 만 원래 색, 나머지는 회색 dim — 사용자가 "이 구간만 보고 싶다" 의도 반영.
  const ranges = heatRanges.value.map((r, idx) => ({
    ...r,
    color: activeRangeIdx.value === null || activeRangeIdx.value === idx
      ? r.color
      : '#f5f5f5',  // dim
  }));
  return {
    chart: { id: 'heatmap', toolbar: { show: false } },
    legend: { show: false },  // 기본 범례 숨김 — 위 커스텀 범례 사용
    dataLabels: { enabled: false },
    xaxis: { type: 'category', title: { text: '시각 (시, KST)' } },
    plotOptions: {
      heatmap: {
        shadeIntensity: 0.5,
        colorScale: { ranges },
      },
    },
    tooltip: {
      y: {
        formatter: (val: number) =>
          heatMode.value === 'count' ? `${val} 호출` : `${val.toFixed(1)}%`,
      },
    },
  };
});

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

<style scoped>
.heatmap-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 16px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  font-size: 0.8rem;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
  background: rgba(255, 255, 255, 0.6);
}
.legend-chip:hover {
  border-color: rgba(0, 0, 0, 0.4);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
  transform: translateY(-1px);
}
.legend-chip.active {
  border-color: rgb(var(--v-theme-primary));
  box-shadow: 0 2px 8px rgba(25, 118, 210, 0.3);
  font-weight: 600;
}
.legend-chip.dimmed {
  opacity: 0.35;
}
.legend-swatch {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 3px;
  border: 1px solid rgba(0, 0, 0, 0.1);
}
</style>