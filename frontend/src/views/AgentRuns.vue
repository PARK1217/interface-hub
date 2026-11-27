<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">에이전트 실행 기록</h2>
      <v-chip class="ml-3" size="small" color="grey" variant="tonal" prepend-icon="mdi-shield-lock-outline">
        ADMIN · VIEWER · 요약은 가려서 저장
      </v-chip>
      <v-spacer />
      <v-btn variant="text" prepend-icon="mdi-graph-outline" @click="showGraph = !showGraph">
        {{ showGraph ? '구성도 닫기' : '에이전트 구성도' }}
      </v-btn>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="reload">새로고침</v-btn>
    </div>

    <v-expand-transition>
      <v-card v-if="showGraph" class="mb-4">
        <v-card-title class="text-subtitle-1">에이전트 구성 (LangGraph)</v-card-title>
        <v-card-text>
          <div ref="overviewEl" class="mermaid-box" />
        </v-card-text>
      </v-card>
    </v-expand-transition>

    <!-- 에이전트별·모델별 사용량 -->
    <v-card class="mb-4">
      <v-card-title class="d-flex align-center">
        <span class="text-subtitle-1">에이전트·모델별 토큰과 비용</span>
        <v-spacer />
        <v-btn-toggle v-model="period" density="compact" mandatory color="primary" variant="outlined">
          <v-btn value="today">오늘</v-btn>
          <v-btn value="7d">7일</v-btn>
          <v-btn value="30d">30일</v-btn>
        </v-btn-toggle>
      </v-card-title>
      <v-card-text>
        <div v-if="usage" class="d-flex flex-wrap ga-3 mb-3">
          <v-chip variant="tonal" prepend-icon="mdi-format-list-numbered">요청 {{ usage.runs }}건</v-chip>
          <v-chip variant="tonal" prepend-icon="mdi-arrow-down">입력 토큰 {{ num(usage.total_input_tokens) }}</v-chip>
          <v-chip variant="tonal" prepend-icon="mdi-arrow-up">출력 토큰 {{ num(usage.total_output_tokens) }}</v-chip>
          <v-chip variant="tonal" prepend-icon="mdi-currency-krw">비용 {{ won(usage.total_cost_krw) }}</v-chip>
          <v-chip v-if="usageUnpriced > 0" color="warning" variant="tonal" prepend-icon="mdi-alert-outline">
            단가 확인 필요 {{ usageUnpriced }}건 (비용 합계에서 빠짐)
          </v-chip>
        </div>
        <v-data-table
          :headers="usageHeaders"
          :items="usage?.rows ?? []"
          density="compact"
          :items-per-page="-1"
          hide-default-footer
          no-data-text="이 기간에 LLM 호출이 없습니다 (LLM 미설정이면 모든 답이 fallback 모드)"
        >
          <template #item.agent_id="{ item }">{{ agentLabel(item.agent_id) }}</template>
          <template #item.input_tokens="{ item }">{{ num(item.input_tokens) }}</template>
          <template #item.output_tokens="{ item }">{{ num(item.output_tokens) }}</template>
          <template #item.cost_krw="{ item }">
            <span v-if="item.cost_krw !== null">{{ won(item.cost_krw) }}</span>
            <v-chip v-else size="x-small" color="warning" variant="tonal">단가 확인 필요</v-chip>
            <span v-if="item.cost_krw !== null && item.unpriced_calls > 0" class="text-caption text-warning ml-1">
              (+{{ item.unpriced_calls }}건 단가 없음)
            </span>
          </template>
        </v-data-table>
      </v-card-text>
    </v-card>

    <!-- 요청 목록 -->
    <v-card>
      <v-card-title class="d-flex align-center flex-wrap ga-2">
        <span class="text-subtitle-1">요청 목록 (최근 순)</span>
        <v-spacer />
        <v-select
          v-model="filter.route"
          :items="routeOptions"
          item-title="label"
          item-value="value"
          label="경로"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          style="max-width: 200px"
        />
        <v-select
          v-model="filter.status"
          :items="statusOptions"
          item-title="label"
          item-value="value"
          label="상태"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          style="max-width: 160px"
        />
      </v-card-title>
      <v-data-table
        :headers="runHeaders"
        :items="runs"
        :loading="loading"
        density="compact"
        :items-per-page="25"
        hover
        @click:row="onRowClick"
      >
        <template #item.started_at="{ item }"><span class="text-caption">{{ fmt(item.started_at) }}</span></template>
        <template #item.entry="{ item }">
          <v-chip size="x-small" variant="outlined">{{ item.entry === 'incident' ? '장애 화면' : '채팅' }}</v-chip>
        </template>
        <template #item.request_summary="{ item }">
          <span class="text-truncate d-inline-block" style="max-width: 360px">{{ item.request_summary }}</span>
        </template>
        <template #item.route="{ item }">
          <v-chip size="small" :color="routeColor(item.route)" variant="tonal">{{ agentLabel(item.route) }}</v-chip>
        </template>
        <template #item.status="{ item }">
          <v-chip size="small" :color="statusColor(item.status)">{{ statusLabel(item.status) }}</v-chip>
          <span class="text-caption text-medium-emphasis ml-1">{{ item.outcome }}</span>
        </template>
        <template #item.tokens="{ item }">
          <span v-if="item.llm_calls">{{ num(item.total_input_tokens) }} / {{ num(item.total_output_tokens) }}</span>
          <span v-else class="text-caption text-medium-emphasis">LLM 호출 없음</span>
        </template>
        <template #item.total_cost_krw="{ item }">
          <v-chip v-if="item.unpriced_llm_calls > 0" size="x-small" color="warning" variant="tonal">단가 확인 필요</v-chip>
          <span v-else-if="item.llm_calls">{{ won(item.total_cost_krw) }}</span>
          <span v-else class="text-caption text-medium-emphasis">-</span>
        </template>
        <template #item.duration_ms="{ item }">{{ item.duration_ms ?? '-' }} ms</template>
      </v-data-table>
    </v-card>

    <!-- 실행 흐름 상세 -->
    <v-dialog v-model="detailOpen" max-width="1200" scrollable>
      <v-card v-if="detail">
        <v-card-title class="d-flex align-center flex-wrap ga-2">
          <span>실행 흐름</span>
          <v-chip size="small" :color="routeColor(detail.run.route)" variant="tonal">{{ agentLabel(detail.run.route) }}</v-chip>
          <v-chip size="small" :color="statusColor(detail.run.status)">{{ statusLabel(detail.run.status) }}</v-chip>
          <v-chip size="small" variant="outlined">{{ detail.run.outcome }}</v-chip>
          <v-spacer />
          <span class="text-caption text-medium-emphasis">{{ fmt(detail.run.started_at) }} · {{ detail.run.duration_ms }} ms</span>
        </v-card-title>
        <v-card-subtitle class="text-wrap">{{ detail.run.request_summary }}</v-card-subtitle>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="5">
              <div class="text-subtitle-2 mb-2">지나간 경로</div>
              <div ref="pathEl" class="mermaid-box" />
              <div class="d-flex flex-wrap ga-2 mt-3">
                <v-chip size="small" variant="tonal" prepend-icon="mdi-arrow-down">입력 {{ num(detail.run.total_input_tokens) }}</v-chip>
                <v-chip size="small" variant="tonal" prepend-icon="mdi-arrow-up">출력 {{ num(detail.run.total_output_tokens) }}</v-chip>
                <v-chip v-if="detail.run.unpriced_llm_calls > 0" size="small" color="warning" variant="tonal">단가 확인 필요</v-chip>
                <v-chip v-else size="small" variant="tonal" prepend-icon="mdi-currency-krw">{{ won(detail.run.total_cost_krw) }}</v-chip>
              </div>
            </v-col>
            <v-col cols="12" md="7">
              <div class="text-subtitle-2 mb-2">단계 ({{ detail.steps.length }})</div>
              <v-expansion-panels variant="accordion" multiple>
                <v-expansion-panel v-for="s in detail.steps" :key="s.step_id">
                  <v-expansion-panel-title>
                    <div class="d-flex align-center ga-2 w-100" :style="{ paddingLeft: `${depth(s) * 16}px` }">
                      <span class="text-caption text-medium-emphasis" style="width: 22px">{{ s.seq }}</span>
                      <v-icon size="small" :icon="kindIcon(s.kind)" :color="s.error ? 'error' : undefined" />
                      <strong class="text-body-2">{{ stepTitle(s) }}</strong>
                      <v-chip v-if="s.model" size="x-small" variant="outlined">{{ s.model }}</v-chip>
                      <v-spacer />
                      <span v-if="s.input_tokens !== null" class="text-caption">
                        {{ num(s.input_tokens) }}/{{ num(s.output_tokens ?? 0) }} tok
                      </span>
                      <span v-else-if="s.kind === 'llm' && !s.error" class="text-caption text-warning">토큰 미제공</span>
                      <span class="text-caption text-medium-emphasis ml-2">{{ s.latency_ms ?? 0 }} ms</span>
                    </div>
                  </v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <dl class="step-dl">
                      <dt>종류</dt><dd>{{ kindLabel(s.kind) }} · {{ s.name }}</dd>
                      <dt v-if="s.agent_id">에이전트</dt><dd v-if="s.agent_id">{{ agentLabel(s.agent_id) }}</dd>
                      <dt>받은 내용</dt><dd><pre>{{ s.input_summary || '-' }}</pre></dd>
                      <dt>낸 결과</dt><dd><pre>{{ s.output_summary || '-' }}</pre></dd>
                      <template v-if="s.kind === 'llm'">
                        <dt>토큰 (입/출)</dt>
                        <dd>{{ s.input_tokens ?? '미제공' }} / {{ s.output_tokens ?? '미제공' }}</dd>
                        <dt>비용</dt><dd>{{ s.cost_krw !== null ? won(s.cost_krw) : '단가 확인 필요' }}</dd>
                      </template>
                      <dt v-if="s.error">오류</dt><dd v-if="s.error" class="text-error">{{ s.error }}</dd>
                    </dl>
                  </v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
            </v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="closeRun">닫기</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { AgentRuns, type AgentRunItem, type AgentStepItem, type AgentUsage } from '@/api/client';
import { formatDateTime } from '@/utils/format';

const route = useRoute();
const router = useRouter();

const labels = ref<Record<string, string>>({});
const graphText = ref('');
const showGraph = ref(false);
const overviewEl = ref<HTMLElement | null>(null);
const pathEl = ref<HTMLElement | null>(null);

const period = ref<'today' | '7d' | '30d'>('7d');
const usage = ref<AgentUsage | null>(null);
const usageUnpriced = computed(() => (usage.value?.rows ?? []).reduce((a, r) => a + r.unpriced_calls, 0));

const runs = ref<AgentRunItem[]>([]);
const loading = ref(false);
const filter = reactive<{ route: string | null; status: string | null }>({ route: null, status: null });

const detailOpen = ref(false);
const detail = ref<{ run: AgentRunItem; steps: AgentStepItem[]; path: string[] } | null>(null);

const usageHeaders = [
  { title: '에이전트', key: 'agent_id' },
  { title: '모델', key: 'model' },
  { title: '호출', key: 'calls', align: 'end' as const },
  { title: '입력 토큰', key: 'input_tokens', align: 'end' as const },
  { title: '출력 토큰', key: 'output_tokens', align: 'end' as const },
  { title: '비용', key: 'cost_krw', align: 'end' as const },
];
const runHeaders = [
  { title: '시각', key: 'started_at', width: 160 },
  { title: '진입', key: 'entry', width: 90 },
  { title: '요청 요약', key: 'request_summary' },
  { title: '경로', key: 'route', width: 150 },
  { title: '상태', key: 'status', width: 170 },
  { title: '토큰 (입/출)', key: 'tokens', align: 'end' as const, sortable: false },
  { title: '비용', key: 'total_cost_krw', align: 'end' as const },
  { title: '걸린 시간', key: 'duration_ms', align: 'end' as const },
];
const routeOptions = [
  { value: 'stats_agent', label: '통계 분석' },
  { value: 'config_agent', label: '설정 점검' },
  { value: 'case_agent', label: '장애 사례 검색' },
  { value: 'rca_agent', label: '장애 원인 분석' },
  { value: 'clarify', label: '되묻기' },
  { value: 'guard', label: '앞단 가드' },
  { value: 'cache', label: '캐시 적중' },
];
const statusOptions = [
  { value: 'ok', label: '완료' },
  { value: 'handoff', label: '넘김·찾지 못함' },
  { value: 'refused', label: '차단' },
  { value: 'error', label: '오류' },
  { value: 'running', label: '실행 중' },
];

function fmt(iso: string) {
  return formatDateTime(iso);
}
function num(n: number | null | undefined) {
  return (n ?? 0).toLocaleString('ko-KR');
}
function won(v: string | number | null) {
  const n = Number(v ?? 0);
  return `₩${n.toLocaleString('ko-KR', { maximumFractionDigits: n < 10 ? 2 : 0 })}`;
}
function agentLabel(id: string | null) {
  if (!id) return '-';
  if (id === 'cache') return '캐시 적중';
  return labels.value[id] ?? id;
}
function routeColor(id: string | null) {
  return ({ stats_agent: 'deep-purple', config_agent: 'blue-grey', case_agent: 'teal', rca_agent: 'deep-orange',
    clarify: 'amber', guard: 'grey', cache: 'cyan' } as Record<string, string>)[id ?? ''] ?? 'grey';
}
function statusColor(s: string) {
  return ({ ok: 'success', handoff: 'warning', refused: 'grey', error: 'error', running: 'info' } as Record<string, string>)[s] ?? 'grey';
}
function statusLabel(s: string) {
  return statusOptions.find((o) => o.value === s)?.label ?? s;
}
function kindIcon(k: string) {
  return ({ guard: 'mdi-shield-check-outline', router: 'mdi-sign-direction', agent: 'mdi-robot-outline',
    tool: 'mdi-wrench-outline', llm: 'mdi-brain', finalize: 'mdi-flag-checkered' } as Record<string, string>)[k] ?? 'mdi-circle-small';
}
function kindLabel(k: string) {
  return ({ guard: '앞단 가드', router: '안내(라우터)', agent: '에이전트', tool: '도구', llm: 'LLM 호출', finalize: '마무리' } as Record<string, string>)[k] ?? k;
}
function stepTitle(s: AgentStepItem) {
  if (s.kind === 'agent' || s.kind === 'router') return agentLabel(s.agent_id ?? s.name);
  if (s.kind === 'llm') return `LLM · ${s.name.split(':')[0]}`;
  return s.name;
}
function depth(s: AgentStepItem): number {
  let d = 0;
  let cur = s;
  const byId = new Map((detail.value?.steps ?? []).map((x) => [x.step_id, x]));
  while (cur.parent_step_id && byId.has(cur.parent_step_id) && d < 5) {
    cur = byId.get(cur.parent_step_id)!;
    d += 1;
  }
  return d;
}

// --- mermaid (필요할 때만 불러옴) ---
let mermaidMod: typeof import('mermaid').default | null = null;
let renderSeq = 0;
async function renderMermaid(el: HTMLElement | null, text: string) {
  if (!el || !text) return;
  if (!mermaidMod) {
    mermaidMod = (await import('mermaid')).default;
    mermaidMod.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral' });
  }
  renderSeq += 1;
  const { svg } = await mermaidMod.render(`agent-graph-${renderSeq}`, text);
  el.innerHTML = svg;
}
function highlighted(path: string[]) {
  const visited = path.filter((n) => n !== '__start__' && n !== '__end__');
  return `${graphText.value}\n\tclassDef visited fill:#ffcc80,stroke:#e65100,stroke-width:2px;\n\tclass ${visited.join(',')} visited;\n`;
}

async function loadGraph() {
  if (graphText.value) return;
  const res = await AgentRuns.graph();
  graphText.value = res.data.mermaid;
  labels.value = res.data.labels;
}
async function loadUsage() {
  usage.value = (await AgentRuns.usage(period.value)).data;
}
async function loadRuns() {
  loading.value = true;
  try {
    runs.value = (await AgentRuns.list({ limit: 200, route: filter.route || undefined, status: filter.status || undefined })).data;
  } finally {
    loading.value = false;
  }
}
async function reload() {
  await Promise.all([loadUsage(), loadRuns()]);
}

async function openRun(runId: string) {
  await loadGraph();
  detail.value = (await AgentRuns.get(runId)).data;
  detailOpen.value = true;
  if (route.query.run !== runId) router.replace({ query: { ...route.query, run: runId } });
  await nextTick();
  await renderMermaid(pathEl.value, highlighted(detail.value.path));
}
function onRowClick(_e: unknown, row: { item: AgentRunItem }) {
  openRun(row.item.run_id);
}
function closeRun() {
  detailOpen.value = false;
}
watch(detailOpen, (open) => {
  if (!open && route.query.run) {
    const q = { ...route.query };
    delete q.run;
    router.replace({ query: q });
  }
});
watch(period, loadUsage);
watch(() => [filter.route, filter.status], loadRuns);
watch(showGraph, async (v) => {
  if (!v) return;
  await loadGraph();
  await nextTick();
  await renderMermaid(overviewEl.value, graphText.value);
});

onMounted(async () => {
  await Promise.all([loadGraph(), reload()]);
  const q = route.query.run;
  if (typeof q === 'string' && q) await openRun(q);
});
</script>

<style scoped>
.mermaid-box {
  overflow-x: auto;
  min-height: 120px;
}
.mermaid-box :deep(svg) {
  max-width: 100%;
  height: auto;
}
.step-dl {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 4px 12px;
  font-size: 0.85rem;
}
.step-dl dt {
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.step-dl dd {
  margin: 0;
}
.step-dl pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  margin: 0;
}
</style>
