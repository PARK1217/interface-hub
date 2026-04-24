<template>
  <div>
    <div class="d-flex align-center mb-3">
      <h2 class="text-h5">자동 복구 분석</h2>
      <v-spacer />
      <v-btn-toggle v-model="windowDays" mandatory density="compact" variant="outlined" color="primary">
        <v-btn :value="1" size="small">1일</v-btn>
        <v-btn :value="7" size="small">7일</v-btn>
        <v-btn :value="30" size="small">30일</v-btn>
      </v-btn-toggle>
      <v-switch
        v-model="onlyWithPolicy"
        label="정책 설정된 인터페이스만"
        hide-details
        density="compact"
        color="primary"
        class="ml-4 retry-filter-switch"
        @update:model-value="loadRows"
      />
      <v-btn icon="mdi-refresh" size="small" variant="text" class="ml-2" @click="loadAll" />
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      재시도 정책의 실제 효과를 분석 — <strong>복구율</strong> = 재시도 발생 호출 중 최종 성공 비율.
      60% 이상이면 정책 유지, 20% 미만이면 재검토 권장.
    </v-alert>

    <!-- KPI 카드 -->
    <v-row v-if="summary" dense class="mb-4">
      <v-col cols="12" md="3">
        <v-card height="100%">
          <v-card-text>
            <div class="text-overline text-medium-emphasis">총 호출 ({{ windowDays }}일)</div>
            <div class="text-h4 kpi-value">{{ summary.total_calls.toLocaleString() }}</div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card height="100%">
          <v-card-text>
            <div class="text-overline text-medium-emphasis">재시도 발생</div>
            <div class="text-h4 kpi-value text-warning">{{ summary.multi_attempt_calls.toLocaleString() }}</div>
            <div class="text-caption text-medium-emphasis">
              전체의 {{ ((summary.multi_attempt_calls / Math.max(summary.total_calls, 1)) * 100).toFixed(1) }}%
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card height="100%">
          <v-card-text>
            <div class="text-overline text-medium-emphasis">재시도로 복구</div>
            <div class="text-h4 kpi-value text-success">{{ summary.recovered_calls.toLocaleString() }}</div>
            <div class="text-caption text-medium-emphasis">
              복구율 {{ (summary.recovery_rate * 100).toFixed(1) }}%
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card height="100%">
          <v-card-text>
            <div class="text-overline text-medium-emphasis">재시도해도 실패</div>
            <div class="text-h4 kpi-value text-error">{{ summary.failed_after_retry.toLocaleString() }}</div>
            <div class="text-caption text-medium-emphasis">
              정책 설정 인터페이스 {{ summary.interfaces_with_retry }}/{{ summary.interfaces_total }}
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 효과 강조 배너 -->
    <v-alert
      v-if="summary && summary.recovered_calls > 0"
      type="success"
      variant="tonal"
      density="compact"
      class="mb-4"
      icon="mdi-trending-up"
    >
      <strong>재시도 도입 효과:</strong> 최근 {{ windowDays }}일간
      <strong class="text-success">{{ summary.recovered_calls.toLocaleString() }}건</strong>
      이 외부 일시 장애에도 자동 재시도로 복구 — 만약 재시도 미적용이었다면 모두 실패로 기록됐을 호출.
      운영 SLA <strong>{{ ((summary.recovered_calls / Math.max(summary.total_calls, 1)) * 100).toFixed(2) }}%p</strong> 개선 효과.
    </v-alert>

    <!-- 인터페이스별 효과 표 -->
    <v-card>
      <v-card-title class="d-flex align-center">
        인터페이스별 자동 복구 효과
        <v-chip size="small" class="ml-2" variant="tonal">
          {{ rows.length }}건
        </v-chip>
      </v-card-title>
      <v-data-table
        :headers="headers"
        :items="rows"
        :loading="loading"
        density="comfortable"
        :items-per-page="20"
        :sort-by="[{ key: 'recovered_calls', order: 'desc' }]"
      >
        <template #item.policy="{ item }">
          <v-chip
            v-if="item.retry_max > 0"
            size="x-small"
            color="primary"
            variant="flat"
            prepend-icon="mdi-replay"
          >
            ×{{ item.retry_max }} · {{ item.retry_backoff_seconds }}s
          </v-chip>
          <v-chip v-else size="x-small" color="grey" variant="outlined">
            정책 없음
          </v-chip>
        </template>
        <template #item.total_calls="{ item }">
          {{ item.total_calls.toLocaleString() }}
        </template>
        <template #item.multi_attempt_calls="{ item }">
          <span v-if="item.multi_attempt_calls > 0">
            {{ item.multi_attempt_calls.toLocaleString() }}
            <span class="text-caption text-medium-emphasis">
              ({{ multiAttemptRate(item).toFixed(1) }}%)
            </span>
          </span>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.recovered_calls="{ item }">
          <v-chip
            v-if="item.recovered_calls > 0"
            size="x-small"
            color="success"
            variant="flat"
            prepend-icon="mdi-check-circle"
          >
            {{ item.recovered_calls.toLocaleString() }}
          </v-chip>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.recovery_rate="{ item }">
          <div v-if="item.multi_attempt_calls > 0" style="min-width: 120px">
            <div class="d-flex align-center">
              <v-progress-linear
                :model-value="recoveryRate(item)"
                :color="recoveryColor(item)"
                height="14"
                rounded
                style="flex: 1"
              />
              <span class="text-caption ml-2" style="min-width: 42px; text-align: right">
                {{ recoveryRate(item).toFixed(0) }}%
              </span>
            </div>
            <div class="text-caption text-medium-emphasis mt-1">
              {{ recoveryVerdict(item) }}
            </div>
          </div>
          <span v-else class="text-caption text-medium-emphasis">— 재시도 발생 안함 —</span>
        </template>
        <template #item.failed_after_retry="{ item }">
          <span v-if="item.failed_after_retry > 0" class="text-error">
            {{ item.failed_after_retry.toLocaleString() }}
          </span>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.avg_attempts="{ item }">
          {{ item.avg_attempts.toFixed(2) }}
        </template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { Performance, type RetryEffectRow, type RetryEffectSummary } from '@/api/client';

const summary = ref<RetryEffectSummary | null>(null);
const rows = ref<RetryEffectRow[]>([]);
const loading = ref(false);
const windowDays = ref(7);
const onlyWithPolicy = ref(false);

const headers = [
  { title: '인터페이스', key: 'interface_name' },
  { title: '기관', key: 'organization' },
  { title: '정책', key: 'policy', sortable: false, width: 130 },
  { title: '총 호출', key: 'total_calls', align: 'end' as const },
  { title: '재시도 발생', key: 'multi_attempt_calls', align: 'end' as const, width: 140 },
  { title: '복구', key: 'recovered_calls', align: 'end' as const },
  { title: '복구율', key: 'recovery_rate', sortable: false, width: 220 },
  { title: '최종 실패', key: 'failed_after_retry', align: 'end' as const },
  { title: '평균 시도', key: 'avg_attempts', align: 'end' as const, width: 100 },
];

async function loadAll() {
  loading.value = true;
  try {
    const [s, r] = await Promise.all([
      Performance.retryEffectsSummary(windowDays.value),
      Performance.retryEffects(windowDays.value, onlyWithPolicy.value),
    ]);
    summary.value = s.data;
    rows.value = r.data;
  } finally {
    loading.value = false;
  }
}

async function loadRows() {
  loading.value = true;
  try {
    rows.value = (await Performance.retryEffects(windowDays.value, onlyWithPolicy.value)).data;
  } finally {
    loading.value = false;
  }
}

watch(windowDays, loadAll);
onMounted(loadAll);

function multiAttemptRate(r: RetryEffectRow): number {
  return r.total_calls > 0 ? (r.multi_attempt_calls / r.total_calls) * 100 : 0;
}

function recoveryRate(r: RetryEffectRow): number {
  return r.multi_attempt_calls > 0 ? (r.recovered_calls / r.multi_attempt_calls) * 100 : 0;
}

function recoveryColor(r: RetryEffectRow): string {
  const rate = recoveryRate(r);
  if (rate >= 60) return 'success';
  if (rate >= 30) return 'warning';
  return 'error';
}

function recoveryVerdict(r: RetryEffectRow): string {
  const rate = recoveryRate(r);
  if (rate >= 60) return '효과적 — 정책 유지';
  if (rate >= 30) return '보통 — backoff 조정 검토';
  return '낮음 — retry_max 줄이거나 정책 재검토';
}
</script>

<style scoped>
/* v-switch 가 flex 컨테이너 안에서 짜부러져 라벨이 세로로 wrap 되는 것 방지 */
.retry-filter-switch {
  flex: 0 0 auto;
}
.retry-filter-switch :deep(.v-label) {
  white-space: nowrap;
}
</style>
