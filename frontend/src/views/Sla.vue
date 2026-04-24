<template>
  <div class="sla-report">
    <div class="d-flex align-center mb-4 no-print">
      <h2 class="text-h5">SLA 리포트</h2>
      <v-spacer />
      <v-select
        v-model="days"
        :items="periodOptions"
        item-title="label"
        item-value="value"
        label="기간"
        density="compact"
        hide-details
        style="max-width:180px"
        class="mr-2"
        @update:model-value="load"
      />
      <v-btn
        prepend-icon="mdi-microsoft-excel"
        color="success"
        variant="elevated"
        size="small"
        :href="excelHref"
        download
      >
        Excel
      </v-btn>
      <v-btn
        prepend-icon="mdi-printer"
        color="primary"
        variant="tonal"
        size="small"
        class="ml-2"
        @click="printReport"
      >
        프린트 / PDF 저장
      </v-btn>
    </div>

    <!-- Print-only header -->
    <div class="print-only mb-3">
      <h2>NOA Interface Hub — SLA 리포트</h2>
      <div class="text-caption">
        기간: 최근 {{ days }}일 · 생성: {{ printedAt }}
      </div>
    </div>

    <!-- Section 1: KPI summary -->
    <v-row class="mb-2">
      <v-col cols="12" md="3" v-for="kpi in kpis" :key="kpi.label">
        <v-card variant="elevated">
          <v-card-text>
            <div class="text-caption text-medium-emphasis">{{ kpi.label }}</div>
            <div class="text-h5 mt-1" :class="kpi.color">{{ kpi.value }}</div>
            <div class="text-caption text-medium-emphasis mt-1">{{ kpi.hint }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- Section 2: calendar heatmap -->
    <v-card class="mb-4">
      <v-card-title class="text-subtitle-1 d-flex align-center flex-wrap">
        <span>일별 SLA 충족 캘린더</span>
        <v-chip
          v-if="days > 90"
          class="ml-3"
          size="x-small"
          color="info"
          variant="tonal"
          prepend-icon="mdi-information-outline"
        >
          시각 가독성 한계로 최근 90일만 표시 — 전체 기간 데이터는 Excel 참조
        </v-chip>
      </v-card-title>
      <v-card-text>
        <!-- 커스텀 범례 — 호버 강조, 클릭 시 해당 구간만 컬러로 표시 (필터). 다시 클릭하면 해제. -->
        <div class="cal-legend mb-3" v-if="calSeries.length">
          <span
            v-for="(r, idx) in calRanges"
            :key="r.name"
            class="legend-chip"
            :class="{ active: activeCalIdx === idx, dimmed: activeCalIdx !== null && activeCalIdx !== idx }"
            :title="activeCalIdx === idx ? '클릭해 필터 해제' : `${r.name} 만 강조`"
            @click="toggleCalRange(idx)"
          >
            <span class="legend-swatch" :style="{ background: r.color }" />
            {{ r.name }}
          </span>
          <v-btn
            v-if="activeCalIdx !== null"
            size="x-small"
            variant="text"
            prepend-icon="mdi-close"
            class="ml-2"
            @click="activeCalIdx = null"
          >
            필터 해제
          </v-btn>
        </div>
        <apexchart
          v-if="calSeries.length"
          ref="calChartRef"
          type="heatmap"
          :height="60 + 30 * calSeries.length"
          :options="calOpts"
          :series="calSeries"
        />
        <div v-else class="text-medium-emphasis">데이터 없음</div>
      </v-card-text>
    </v-card>

    <!-- Section 3: summary table -->
    <v-card class="mb-4">
      <v-card-title class="text-subtitle-1">인터페이스별 누적 ({{ days }}일)</v-card-title>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
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
        <template #item.uptime_pct="{ item }">
          <v-chip size="small" :color="item.meets_uptime ? 'success' : 'error'">
            {{ item.uptime_pct.toFixed(2) }}% / 목표 {{ item.target_uptime }}%
          </v-chip>
        </template>
        <template #item.avg_response_ms="{ item }">
          <v-chip size="small" :color="item.meets_response ? 'success' : 'warning'">
            {{ item.avg_response_ms.toFixed(0) }}ms / 목표 {{ item.target_response_ms }}ms
          </v-chip>
        </template>
      </v-data-table>
    </v-card>

    <!-- Section 4: 장기 추이 (위 KPI/캘린더/표는 선택 기간만, 이 차트는 더 긴 시점 흐름) -->
    <v-card>
      <v-card-title class="d-flex align-center flex-wrap">
        <span class="text-subtitle-1">
          {{ trendBucket === 'quarter' ? '분기별' : '월별' }} 가동률 추이
        </span>
        <v-chip class="ml-3" size="x-small" variant="tonal" color="grey">
          최근 {{ trendMonths }}{{ trendBucket === 'quarter' ? '분기' : '개월' }}
        </v-chip>
      </v-card-title>
      <v-card-subtitle>
        위 표·캘린더는 선택 기간({{ days }}일) 내 데이터지만, 이 차트는 <strong>더 긴 시점의 흐름</strong>을
        보여줍니다. 단기 변화는 캘린더에서, 장기 패턴은 이 추이로 — 두 시각이 보완됩니다.
      </v-card-subtitle>
      <v-card-text>
        <apexchart
          v-if="trendSeries.length"
          ref="trendChartRef"
          type="bar"
          height="360"
          :options="trendOpts"
          :series="trendSeries"
        />
        <div v-else class="text-medium-emphasis">집계할 데이터가 없습니다.</div>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  Sla,
  type SlaCalendarCell,
  type SlaReportRow,
  type SlaTrendPoint,
} from '@/api/client';

const headers = [
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '이름', key: 'interface_name' },
  { title: '가동률 (Uptime)', key: 'uptime_pct' },
  { title: '평균 응답', key: 'avg_response_ms' },
];

const periodOptions = [
  { value: 7, label: '최근 7일' },
  { value: 14, label: '최근 14일' },
  { value: 30, label: '최근 30일' },
  { value: 60, label: '최근 60일' },
  { value: 90, label: '최근 90일 (분기)' },
  { value: 180, label: '최근 180일 (반기)' },
  { value: 365, label: '최근 365일 (연간)' },
];

const rows = ref<SlaReportRow[]>([]);
const trendRows = ref<SlaTrendPoint[]>([]);
const calRows = ref<SlaCalendarCell[]>([]);
const days = ref(30);
const loading = ref(false);
const printedAt = ref('');

// 선택 기간에 따라 추이 bucket 자동 선택:
//   짧은 기간 → 월별 (granular), 180일 초과 → 분기별 (덜 빽빽).
// 차트 타입은 안 바뀌고 (위 주석 참조) bucket 단위만 바뀜.
const trendBucket = computed<'month' | 'quarter'>(() =>
  days.value > 180 ? 'quarter' : 'month',
);
// 최소 6개월 — 추이 차트는 "장기 패턴" 용도라 너무 짧으면 의미 없음.
// 365일 선택 시엔 12개월 이상까지 늘림.
const trendMonths = computed(() =>
  Math.max(6, Math.min(24, Math.ceil(days.value / 30) + 3)),
);
// 캘린더 히트맵은 시각 가독성 한계로 **항상 최근 90일까지만** 표시.
// 그 이상은 컬럼이 5px 이하로 줄어들어 사람이 못 읽음. 더 긴 기간 데이터는
// Excel 다운로드로 안내 (헤더 칩에 표시).
const calendarDays = computed(() => Math.min(90, days.value));

const excelHref = computed(() => Sla.exportXlsxUrl(days.value));

const kpis = computed(() => {
  const total = rows.value.length;
  const archived = rows.value.filter((r) => !!r.deleted_at).length;
  const active = total - archived;
  const okUp = rows.value.filter((r) => r.meets_uptime).length;
  const okResp = rows.value.filter((r) => r.meets_response).length;
  const okBoth = rows.value.filter((r) => r.meets_uptime && r.meets_response).length;
  const avgUp = total ? rows.value.reduce((a, r) => a + r.uptime_pct, 0) / total : 0;
  return [
    {
      label: '집계 인터페이스 수',
      value: total.toString(),
      hint: archived > 0 ? `활성 ${active} + 보관 ${archived}` : `활성 ${active}`,
      color: 'text-primary',
    },
    {
      label: '평균 가동률',
      value: `${avgUp.toFixed(2)}%`,
      hint: '전 인터페이스 평균',
      color: avgUp >= 99 ? 'text-success' : 'text-warning',
    },
    {
      label: '가동률 목표 달성',
      value: `${okUp}/${total}`,
      hint: `${total ? Math.round((okUp / total) * 100) : 0}% 충족`,
      color: okUp === total ? 'text-success' : 'text-warning',
    },
    {
      label: '응답시간 목표 달성',
      value: `${okResp}/${total}`,
      hint: okBoth === total ? '모두 정상' : `${total - okBoth}건 미달`,
      color: okResp === total ? 'text-success' : 'text-warning',
    },
  ];
});

function withArchivedSuffix(name: string, deleted: string | null | undefined): string {
  return deleted ? `${name} (보관)` : name;
}

// ---- trend series (line/bar per interface)
const trendSeries = computed(() => {
  const grouped: Record<string, { name: string; data: { x: string; y: number }[] }> = {};
  for (const p of trendRows.value) {
    const key = `${p.interface_id} ${withArchivedSuffix(p.interface_name, p.deleted_at)}`;
    if (!grouped[key]) grouped[key] = { name: key, data: [] };
    grouped[key].data.push({ x: p.period, y: Number(p.uptime_pct.toFixed(2)) });
  }
  return Object.values(grouped);
});

const trendBucketCount = computed(
  () => new Set(trendRows.value.map((p) => p.period)).size,
);

const trendOpts = computed(() => {
  const ys = trendRows.value.map((p) => p.uptime_pct).filter((v) => Number.isFinite(v));
  // 데이터에 95% 미만 dip 있으면 Y축이 자동으로 85까지 내려가서 안 잘림.
  // 다 99%대면 90~100 으로 줌인되어 차이가 잘 보임.
  const yMin = ys.length ? Math.min(85, Math.floor(Math.min(...ys) - 1)) : 90;
  // 차트 타입은 **항상 그룹 막대** 로 고정. 월별/분기별 보고서의 표준
  // 시각화 형태이고, 운영자가 "SLA 추이는 막대 차트" 로 한 번 학습하면
  // 매번 같은 형태로 인지 가능. 데이터 양에 따라 자동으로 바/라인 전환
  // 시도했었으나 — 같은 화면이 매번 다른 모양으로 나오는 건 BI 안티패턴
  // (Tableau/PowerBI/Datadog 등도 차트 타입은 고정). 데이터가 너무
  // 많아질 때는 차트 형태가 아니라 데이터 자체를 줄임 (Top-N, 평균,
  // 드릴다운).
  return {
    chart: {
      id: 'sla-trend',
      type: 'bar',
      toolbar: { show: false },
      animations: { enabled: false },
      stacked: false,
    },
    stroke: { show: false },
    markers: { size: 0 },
    dataLabels: {
      enabled: trendBucketCount.value <= 6,  // hide labels when too crowded
      formatter: (v: number) => (v == null ? '' : `${v.toFixed(1)}`),
      offsetY: -18,
      style: { fontSize: '10px', colors: ['#444'] },
      background: { enabled: false },
    },
    plotOptions: {
      bar: {
        borderRadius: 4,
        borderRadiusApplication: 'end',
        columnWidth: '85%',
        dataLabels: { position: 'top' },
      },
    },
    xaxis: {
      type: 'category',
      title: { text: trendBucket.value === 'month' ? '월' : '분기' },
      labels: { rotate: -30, hideOverlappingLabels: true, trim: true },
    },
    yaxis: {
      title: { text: '가동률 (%)' },
      min: yMin,
      max: 100,
      tickAmount: 5,
      labels: { formatter: (v: number) => `${v.toFixed(1)}%` },
    },
    legend: {
      position: 'bottom',
      showForSingleSeries: true,
      itemMargin: { horizontal: 8, vertical: 4 },
    },
    tooltip: {
      shared: false,
      intersect: true,
      followCursor: true,
      y: { formatter: (v: number) => (v == null ? '-' : `${v.toFixed(2)}%`) },
    },
    annotations: {
      yaxis: [
        {
          y: 99,
          borderColor: '#E04F5F',
          strokeDashArray: 4,
          label: {
            text: '일반 목표 99%',
            position: 'right',
            style: { color: '#fff', background: '#E04F5F' },
          },
        },
      ],
    },
  };
});

// ---- calendar heatmap series (1 row per interface, columns=days)
const calSeries = computed(() => {
  const grouped: Record<string, { name: string; data: { x: string; y: number }[] }> = {};
  for (const c of calRows.value) {
    const key = `#${c.interface_id} ${withArchivedSuffix(c.interface_name, c.deleted_at)}`;
    if (!grouped[key]) grouped[key] = { name: key, data: [] };
    let score = 0;
    if (c.uptime_pct >= c.target_uptime && c.avg_response_ms <= c.target_response_ms) score = 100;
    else if (c.uptime_pct >= c.target_uptime || c.avg_response_ms <= c.target_response_ms) score = 50;
    grouped[key].data.push({ x: c.date.slice(5), y: score });
  }
  return Object.values(grouped);
});

// 캘린더 범례 — 대시보드 히트맵과 동일 패턴 (커스텀 칩, 클릭 시 필터).
interface CalRange { from: number; to: number; color: string; name: string }
const calRanges: CalRange[] = [
  { from: -1,  to: 0,   color: '#eef2f7', name: '데이터 없음' },
  { from: 1,   to: 49,  color: '#ef9a9a', name: '미달' },
  { from: 50,  to: 99,  color: '#ffcc80', name: '부분 달성' },
  { from: 100, to: 100, color: '#66bb6a', name: '목표 달성' },
];
const activeCalIdx = ref<number | null>(null);
function toggleCalRange(idx: number) {
  activeCalIdx.value = activeCalIdx.value === idx ? null : idx;
}

const calOpts = computed(() => {
  const colCount = calSeries.value[0]?.data.length ?? 0;
  const rotate = colCount > 30 ? -45 : -15;
  const showEvery = colCount > 60 ? 7 : colCount > 30 ? 3 : 1;
  // 활성 범위가 있으면 그 idx 만 원래 색, 나머지는 회색 dim
  const ranges = calRanges.map((r, idx) => ({
    ...r,
    color: activeCalIdx.value === null || activeCalIdx.value === idx ? r.color : '#f5f5f5',
  }));
  return {
    chart: { id: 'sla-cal', toolbar: { show: false }, animations: { enabled: false } },
    dataLabels: { enabled: false },
    stroke: { width: 1, colors: ['#fff'] },
    legend: { show: false },  // 기본 범례 숨김 — 위 커스텀 칩 사용
    xaxis: {
      type: 'category',
      title: { text: '날짜 (MM-DD)' },
      labels: {
        rotate,
        rotateAlways: colCount > 30,
        hideOverlappingLabels: true,
        trim: true,
        formatter: (val: string, _ts: any, opts: any) => {
          const idx = opts?.i ?? 0;
          return idx % showEvery === 0 ? val : '';
        },
      },
    },
    yaxis: {
      labels: { maxWidth: 200, style: { fontSize: '11px' } },
    },
    plotOptions: {
      heatmap: {
        shadeIntensity: 0,
        radius: 2,
        useFillColorAsStroke: false,
        colorScale: { ranges },
      },
    },
    tooltip: {
      custom: ({ seriesIndex, dataPointIndex, w }: any) => {
        const series = w.config.series[seriesIndex];
        const point = series.data[dataPointIndex];
        const score = point.y;
        const label = score === 100 ? '목표 달성' : score >= 50 ? '부분 달성' : score > 0 ? '미달' : '데이터 없음';
        return `<div style="padding:6px 10px"><strong>${series.name}</strong><br>${point.x} → ${label}</div>`;
      },
    },
  };
});

async function load() {
  loading.value = true;
  try {
    const [r, t, c] = await Promise.all([
      Sla.report(days.value),
      Sla.trend(trendBucket.value, trendMonths.value),
      Sla.calendar(calendarDays.value),
    ]);
    rows.value = r.data;
    trendRows.value = t.data;
    calRows.value = c.data;
  } finally {
    loading.value = false;
  }
}

// A4 가로 인쇄 영역에 맞춘 명시적 차트 사이즈
// (≈ 280mm × 200mm, 여백 8mm → ≈ 1050px × 750px @ 96dpi).
// CSS 만으로는 ApexCharts SVG 의 내부 viewBox 가 화면 픽셀로 고정돼서
// 인쇄 시 X축 라벨까지 잘림. beforeprint 시점에 chart.updateOptions 로
// width/height 를 명시적으로 바꿔 차트 자체를 재렌더링해야 함.
const PRINT_WIDTH = 1050;
const PRINT_TREND_H = 380;
const PRINT_CAL_H = 460;

const trendChartRef = ref<any>(null);
const calChartRef = ref<any>(null);

function applyPrintLayout() {
  trendChartRef.value?.updateOptions(
    { chart: { width: PRINT_WIDTH, height: PRINT_TREND_H } },
    false,  // redrawPaths
    false,  // animate
    false,  // updateSyncedCharts
  );
  calChartRef.value?.updateOptions(
    { chart: { width: PRINT_WIDTH, height: PRINT_CAL_H } },
    false,
    false,
    false,
  );
}

function applyScreenLayout() {
  trendChartRef.value?.updateOptions(
    { chart: { width: '100%', height: 360 } }, false, false, false,
  );
  calChartRef.value?.updateOptions(
    { chart: { width: '100%', height: 60 + 30 * calSeries.value.length } },
    false, false, false,
  );
}

function printReport() {
  printedAt.value = new Date().toLocaleString('ko-KR');
  applyPrintLayout();
  // Wait one paint cycle so ApexCharts finishes the redraw at print size
  setTimeout(() => {
    window.print();
    // After print dialog dismissed, restore screen layout
    setTimeout(applyScreenLayout, 300);
  }, 350);
}

function handleBeforePrint() {
  printedAt.value = new Date().toLocaleString('ko-KR');
  applyPrintLayout();
}
function handleAfterPrint() {
  applyScreenLayout();
}

onMounted(() => {
  load();
  if (typeof window !== 'undefined') {
    window.addEventListener('beforeprint', handleBeforePrint);
    window.addEventListener('afterprint', handleAfterPrint);
  }
});

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('beforeprint', handleBeforePrint);
    window.removeEventListener('afterprint', handleAfterPrint);
  }
});
</script>

<style scoped>
/* 캘린더 커스텀 범례 (대시보드 히트맵과 동일 패턴) */
.cal-legend {
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

.print-only {
  display: none;
}

@media print {
  /* Use landscape so wide charts (24-hour, 90-day calendar) actually fit */
  @page {
    size: A4 landscape;
    margin: 10mm;
  }

  /* Hide app chrome */
  .no-print { display: none !important; }
  .print-only { display: block !important; }

  /* Cards: no shadow, page-break friendly, full width */
  :deep(.v-card) {
    box-shadow: none !important;
    border: 1px solid #ddd !important;
    page-break-inside: avoid;
    width: 100% !important;
    max-width: 100% !important;
  }
  :deep(.v-card-text) { padding: 12px !important; }

  /* ApexCharts SVG: force to fit the printable width */
  :deep(.apexcharts-canvas),
  :deep(.apexcharts-canvas svg),
  :deep(.apexcharts-svg) {
    width: 100% !important;
    max-width: 100% !important;
    height: auto !important;
  }

  /* Make tooltips/zoom buttons not bleed into print */
  :deep(.apexcharts-tooltip),
  :deep(.apexcharts-toolbar),
  :deep(.apexcharts-zoom-icon) {
    display: none !important;
  }
}

/* Print-only header style */
.print-only h2 {
  font-size: 18px;
  margin: 0 0 4px 0;
}
</style>
