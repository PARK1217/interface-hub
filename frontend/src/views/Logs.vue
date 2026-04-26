<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">호출 로그 · 재처리</h2>
      <v-spacer />
      <v-chip v-if="selected.length" color="warning" variant="flat" class="mr-3">
        {{ selected.length }}건 선택
      </v-chip>
      <v-btn
        v-if="selected.length && auth.canMutate"
        color="warning"
        prepend-icon="mdi-restart"
        :loading="bulkBusy"
        @click="bulkRetry"
      >
        선택 일괄 재처리
      </v-btn>
      <v-chip v-if="!auth.canMutate" size="small" variant="tonal" color="grey">
        {{ auth.role }} — 재처리 권한 없음
      </v-chip>
    </div>

    <v-card class="mb-4">
      <v-card-text>
        <v-row dense align="center">
          <v-col cols="12" md="2">
            <v-text-field v-model.number="filter.interface_id" label="인터페이스 ID" type="number" clearable density="compact" hide-details />
          </v-col>
          <v-col cols="12" md="2">
            <v-select v-model="filter.status" :items="statuses" label="상태" clearable density="compact" hide-details />
          </v-col>
          <v-col cols="12" md="2">
            <v-select v-model="filter.protocol" :items="protocols" label="프로토콜" clearable density="compact" hide-details />
          </v-col>
          <v-col cols="12" md="4">
            <v-text-field v-model="filter.keyword" label="에러 메시지 키워드" clearable density="compact" hide-details />
          </v-col>
          <v-col cols="12" md="2" class="d-flex align-center" style="padding-left: 20px">
            <v-switch v-model="filter.failedOnly" hide-details color="warning" label="실패만" density="compact" />
          </v-col>
        </v-row>
        <v-divider class="my-3" />
        <div class="d-flex align-center" style="gap: 8px; flex-wrap: wrap">
          <v-text-field
            :model-value="filter.sinceText"
            label="시작"
            placeholder="2026-04-25 09:00"
            density="compact"
            hide-details
            clearable
            class="dt-input dt-input-text"
            :error="!!sinceError"
            @update:model-value="(v) => onDtInput('sinceText', v)"
          />
          <span class="text-caption text-medium-emphasis mx-1">~</span>
          <v-text-field
            :model-value="filter.untilText"
            label="종료"
            placeholder="2026-04-25 18:00"
            density="compact"
            hide-details
            clearable
            class="dt-input dt-input-text"
            :error="!!untilError"
            @update:model-value="(v) => onDtInput('untilText', v)"
          />
          <div class="d-flex align-center ml-3" style="gap: 6px; flex-wrap: wrap; flex: 1">
            <v-chip
              v-for="q in quickRanges"
              :key="q.label"
              size="small"
              :color="activeQuick === q.hours ? 'primary' : ''"
              :variant="activeQuick === q.hours ? 'flat' : 'tonal'"
              @click="applyQuickRange(q.hours)"
            >
              {{ q.label }}
            </v-chip>
            <v-chip
              v-if="hasTimeRange"
              size="small"
              variant="text"
              prepend-icon="mdi-close"
              @click="clearTimeRange"
            >
              해제
            </v-chip>
          </div>
          <v-btn color="primary" @click="load" :loading="loading">검색</v-btn>
        </div>
        <div v-if="sinceError || untilError" class="text-caption text-error mt-1 ml-1">
          형식 오류: <code>YYYY-MM-DD</code> 또는 <code>YYYY-MM-DD HH:mm</code> 으로 입력해주세요.
        </div>
      </v-card-text>
    </v-card>

    <v-card>
      <v-data-table
        v-model="selected"
        :headers="headers"
        :items="rows"
        :loading="loading"
        item-value="id"
        show-select
        density="comfortable"
      >
        <template #item.called_at="{ item }">
          <span class="text-caption">{{ formatDateTime(item.called_at) }}</span>
        </template>
        <template #item.status="{ item }">
          <v-chip size="small" :color="item.status === 'SUCCESS' ? 'success' : 'error'">{{ item.status }}</v-chip>
        </template>
        <template #item.duration_ms="{ item }">{{ item.duration_ms }} ms</template>
        <template #item.triggered_by="{ item }">
          <v-chip size="x-small" :color="triggerColor(item.triggered_by)" variant="tonal">
            {{ triggerLabel(item.triggered_by) }}
          </v-chip>
        </template>
        <template #item.lineage="{ item }">
          <v-chip
            v-if="item.parent_log_id"
            size="x-small"
            color="info"
            variant="tonal"
            :title="`원본 #${item.parent_log_id}의 ${item.retry_count}번째 재처리`"
          >
            ↻ retry #{{ item.retry_count }} of #{{ item.parent_log_id }}
          </v-chip>
          <v-chip
            v-else-if="item.is_reprocessed"
            size="x-small"
            color="grey"
            variant="tonal"
            title="이 원본은 이미 재처리됨"
          >
            재처리됨
          </v-chip>
          <v-chip
            v-if="(item.attempt_count ?? 1) > 1"
            size="x-small"
            color="amber-darken-2"
            variant="flat"
            class="ml-1"
            prepend-icon="mdi-replay"
            :title="`인터페이스 재시도 정책에 따라 ${item.attempt_count}회 시도 후 종료`"
          >
            ×{{ item.attempt_count }}
          </v-chip>
        </template>
        <template #item.actions="{ item }">
          <div class="d-flex justify-end" style="gap: 4px; min-width: 96px;">
            <v-btn
              icon="mdi-eye-outline"
              size="x-small"
              variant="text"
              color="primary"
              title="상세 보기"
              @click="openDetail(item)"
            />
            <v-btn
              v-if="canRetry(item) && auth.canMutate"
              icon="mdi-restart"
              size="x-small"
              variant="text"
              :loading="retrying === item.id"
              color="warning"
              title="재실행"
              @click="retryOne(item)"
            />
            <div v-else style="width: 28px;" />
            <v-btn
              v-if="item.parent_log_id || item.is_reprocessed"
              icon="mdi-source-branch"
              size="x-small"
              variant="text"
              color="info"
              title="재처리 체인 보기"
              @click="openChain(item.id)"
            />
            <div v-else style="width: 28px;" />
          </div>
        </template>
      </v-data-table>
    </v-card>

    <!-- detail dialog -->
    <v-dialog v-model="detailDialog" max-width="980" scrollable>
      <v-card v-if="detail">
        <v-card-title class="d-flex align-center">
          <span>호출 상세 #{{ detail.id }}</span>
          <v-chip
            class="ml-3"
            size="small"
            :color="detail.status === 'SUCCESS' ? 'success' : 'error'"
          >
            {{ detail.status }}
          </v-chip>
          <v-chip v-if="detail.http_status" class="ml-2" size="small" variant="outlined">
            HTTP {{ detail.http_status }}
          </v-chip>
          <v-spacer />
          <v-btn icon="mdi-content-copy" size="small" variant="text" title="JSON 복사" @click="copyDetail" />
          <v-btn
            v-if="canRetry(detail)"
            color="warning"
            size="small"
            variant="tonal"
            prepend-icon="mdi-restart"
            class="ml-2"
            @click="retryOne(detail); detailDialog = false"
          >
            재실행
          </v-btn>
        </v-card-title>

        <v-card-text>
          <v-row dense>
            <v-col cols="6" md="3">
              <div class="text-caption text-medium-emphasis">인터페이스 ID</div>
              <div>#{{ detail.interface_id }}</div>
            </v-col>
            <v-col cols="6" md="3">
              <div class="text-caption text-medium-emphasis">소요 시간</div>
              <div>{{ detail.duration_ms }} ms</div>
            </v-col>
            <v-col cols="6" md="3">
              <div class="text-caption text-medium-emphasis">트리거</div>
              <div>{{ triggerLabel(detail.triggered_by) }}</div>
            </v-col>
            <v-col cols="6" md="3">
              <div class="text-caption text-medium-emphasis">호출 시각</div>
              <div>{{ formatDateTime(detail.called_at) }}</div>
            </v-col>
            <v-col v-if="detail.parent_log_id || detail.retry_count" cols="12">
              <div class="text-caption text-medium-emphasis">재처리</div>
              <v-chip size="small" color="info" variant="tonal">
                retry #{{ detail.retry_count }} of #{{ detail.parent_log_id }}
              </v-chip>
            </v-col>
          </v-row>

          <!-- Error block — only when failed -->
          <template v-if="detail.error_message || detail.error_type">
            <v-divider class="my-4" />
            <div class="d-flex align-center mb-2">
              <v-icon color="error" icon="mdi-alert-octagon" class="mr-2" />
              <span class="text-subtitle-2">에러 상세</span>
              <v-chip v-if="detail.error_type" size="x-small" color="error" variant="tonal" class="ml-3">
                {{ detail.error_type }}
              </v-chip>
            </div>
            <v-alert
              v-if="detail.error_message"
              type="error"
              variant="tonal"
              density="compact"
              class="mb-2"
            >
              {{ detail.error_message }}
            </v-alert>
            <v-expansion-panels v-if="detail.error_trace" variant="accordion">
              <v-expansion-panel>
                <v-expansion-panel-title>
                  <v-icon icon="mdi-code-tags" size="small" class="mr-2" />
                  스택 트레이스 ({{ (detail.error_trace || '').split('\n').length }}줄)
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <pre class="json-block">{{ detail.error_trace }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </template>

          <v-divider class="my-4" />

          <div class="text-subtitle-2 mb-2">
            <v-icon icon="mdi-arrow-up-bold-circle-outline" size="small" /> Request
          </div>
          <pre class="json-block">{{ pretty(detail.request) }}</pre>

          <div class="text-subtitle-2 mt-4 mb-2">
            <v-icon icon="mdi-arrow-down-bold-circle-outline" size="small" /> Response
            <span v-if="responseHeaders" class="text-caption text-medium-emphasis ml-2">
              (헤더 {{ Object.keys(responseHeaders).length }}개 포함)
            </span>
          </div>
          <pre class="json-block">{{ pretty(detail.response) }}</pre>
        </v-card-text>

        <v-card-actions>
          <v-spacer />
          <v-btn @click="detailDialog = false">닫기</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- chain dialog -->
    <v-dialog v-model="chainDialog" max-width="900">
      <v-card>
        <v-card-title>재처리 체인 (root #{{ chain[0]?.id }})</v-card-title>
        <v-card-text>
          <v-timeline density="compact" side="end">
            <v-timeline-item
              v-for="(node, idx) in chain"
              :key="node.id"
              :dot-color="node.status === 'SUCCESS' ? 'success' : 'error'"
              size="small"
            >
              <div class="d-flex align-center" style="gap:8px">
                <strong>#{{ node.id }}</strong>
                <v-chip size="x-small" :color="node.status === 'SUCCESS' ? 'success' : 'error'">
                  {{ node.status }}
                </v-chip>
                <v-chip v-if="idx === 0" size="x-small" variant="tonal">원본</v-chip>
                <v-chip v-else size="x-small" color="info" variant="tonal">retry #{{ node.retry_count }}</v-chip>
                <span class="text-caption text-medium-emphasis">{{ formatDateTime(node.called_at) }} · {{ node.duration_ms }}ms</span>
              </div>
              <div v-if="node.error_message" class="text-caption text-error mt-1">{{ node.error_message }}</div>
            </v-timeline-item>
          </v-timeline>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="chainDialog = false">닫기</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="3500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { CallLogs, type CallLogItem } from '@/api/client';
import { formatDateTime } from '@/utils/format';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();

const statuses = ['SUCCESS', 'FAILURE', 'TIMEOUT', 'AUTH_ERROR', 'FORMAT_ERROR', 'SERVER_ERROR'];
const protocols = ['REST', 'SOAP', 'FTP', 'MQ', 'BATCH'];
const headers = [
  { title: '시각', key: 'called_at' },
  { title: '인터페이스 ID', key: 'interface_id', width: 110 },
  { title: '상태', key: 'status', width: 110 },
  { title: 'HTTP', key: 'http_status', width: 70 },
  { title: '소요', key: 'duration_ms', width: 80 },
  { title: '트리거', key: 'triggered_by', width: 90 },
  { title: '재처리', key: 'lineage', width: 180, sortable: false },
  { title: '에러', key: 'error_message' },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 100 },
];

const rows = ref<CallLogItem[]>([]);
const selected = ref<number[]>([]);
const loading = ref(false);
const retrying = ref<number | null>(null);
const bulkBusy = ref(false);
const chainDialog = ref(false);
const chain = ref<CallLogItem[]>([]);
const detailDialog = ref(false);
const detail = ref<CallLogItem | null>(null);
const snack = reactive({ show: false, text: '', color: 'success' });

const filter = reactive<{
  interface_id: number | null;
  status: string | null;
  protocol: string | null;
  keyword: string;
  failedOnly: boolean;
  // 단순 텍스트 입력 — 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:mm' 자유 형식.
  // 시간 생략 시 since 는 00:00, until 은 23:59 기본값.
  sinceText: string | null;
  untilText: string | null;
}>({
  interface_id: null, status: null, protocol: null, keyword: '', failedOnly: false,
  sinceText: null, untilText: null,
});

// 빠른 선택 — 지난 N시간/일 구간을 since 에 채움 (until 은 비워서 "지금까지")
const quickRanges = [
  { label: '지난 1시간', hours: 1 },
  { label: '지난 6시간', hours: 6 },
  { label: '지난 24시간', hours: 24 },
  { label: '지난 7일', hours: 24 * 7 },
];
// 빠른 선택 칩 활성 상태 (직접 입력 시 자동 해제)
const activeQuick = ref<number | null>(null);

const hasTimeRange = computed(() => !!(filter.sinceText || filter.untilText));

function pad2(n: number): string { return String(n).padStart(2, '0'); }

// 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:mm' 파싱 → ISO string. 형식 안 맞으면 null + 에러 표시용.
const DT_RE = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2}))?$/;

function parseDT(text: string | null, fallbackTime: [number, number]): string | null | 'INVALID' {
  if (!text || !text.trim()) return null;
  const m = DT_RE.exec(text.trim());
  if (!m) return 'INVALID';
  const [, y, mo, d, h, mi] = m;
  const date = new Date(
    Number(y), Number(mo) - 1, Number(d),
    h !== undefined ? Number(h) : fallbackTime[0],
    mi !== undefined ? Number(mi) : fallbackTime[1],
  );
  if (isNaN(date.getTime())) return 'INVALID';
  return date.toISOString();
}

const sinceError = computed(() => parseDT(filter.sinceText, [0, 0]) === 'INVALID');
const untilError = computed(() => parseDT(filter.untilText, [23, 59]) === 'INVALID');

// 사용자가 숫자만 입력해도 자동으로 'YYYY-MM-DD HH:mm' 형식으로 마스킹.
// '20260425' → '2026-04-25', '202604250900' → '2026-04-25 09:00'.
// 기존 구분자는 무시하고 숫자만 추출 후 자릿수에 따라 다시 채움 (paste 도 자동 정리).
function maskDateTime(input: string | null): string | null {
  if (!input) return null;
  const digits = input.replace(/\D/g, '').slice(0, 12);
  if (!digits) return '';
  let s = digits.slice(0, 4);
  if (digits.length >= 5) s += '-' + digits.slice(4, 6);
  if (digits.length >= 7) s += '-' + digits.slice(6, 8);
  if (digits.length >= 9) s += ' ' + digits.slice(8, 10);
  if (digits.length >= 11) s += ':' + digits.slice(10, 12);
  return s;
}

function onDtInput(field: 'sinceText' | 'untilText', v: string | null) {
  filter[field] = maskDateTime(v);
  activeQuick.value = null;
}

function applyQuickRange(hours: number) {
  if (activeQuick.value === hours) {
    clearTimeRange();
    return;
  }
  const d = new Date(Date.now() - hours * 3600_000);
  filter.sinceText = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  filter.untilText = null;
  activeQuick.value = hours;
  load();
}

function clearTimeRange() {
  filter.sinceText = null;
  filter.untilText = null;
  activeQuick.value = null;
  load();
}

function notify(text: string, color = 'success') {
  Object.assign(snack, { show: true, text, color });
}

function canRetry(item: CallLogItem) {
  return item.status !== 'SUCCESS' && !item.is_reprocessed;
}

function triggerLabel(t: string | undefined): string {
  return ({
    manual: '수동',
    schedule: '스케줄',
    reprocess: '재처리',
    ingest: '외부 수신',
  } as Record<string, string>)[t ?? ''] ?? t ?? '-';
}
function triggerColor(t: string | undefined): string {
  return ({
    manual: 'primary',
    schedule: 'info',
    reprocess: 'warning',
    ingest: 'deep-purple',
  } as Record<string, string>)[t ?? ''] ?? 'grey';
}

async function load() {
  loading.value = true;
  selected.value = [];
  try {
    const params: Record<string, unknown> = { limit: 200 };
    if (filter.interface_id) params.interface_id = filter.interface_id;
    if (filter.status) params.status = filter.status;
    if (filter.protocol) params.protocol = filter.protocol;
    if (filter.keyword) params.keyword = filter.keyword;
    // 시작: 시간 미입력 → 00:00 (그 날 시작부터). 종료: 시간 미입력 → 23:59 (그 날 끝까지).
    const since = parseDT(filter.sinceText, [0, 0]);
    const until = parseDT(filter.untilText, [23, 59]);
    if (since && since !== 'INVALID') params.since = since;
    if (until && until !== 'INVALID') params.until = until;
    let data = (await CallLogs.search(params)).data;
    if (filter.failedOnly) {
      data = data.filter((r) => r.status !== 'SUCCESS' && !r.is_reprocessed);
    }
    rows.value = data;
  } finally {
    loading.value = false;
  }
}

async function retryOne(item: CallLogItem) {
  retrying.value = item.id;
  try {
    const res = await CallLogs.retry(item.id);
    notify(`#${item.id} 재처리 → 새 로그 #${res.data.id} (${res.data.status} ${res.data.duration_ms}ms)`,
      res.data.status === 'SUCCESS' ? 'success' : 'warning');
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '재처리 실패', 'error');
  } finally {
    retrying.value = null;
  }
}

async function bulkRetry() {
  if (!selected.value.length) return;
  if (!confirm(`선택된 ${selected.value.length}건을 재처리할까요?`)) return;
  bulkBusy.value = true;
  try {
    // Backend bulk-retry takes a filter; for explicit selection we loop
    const ids = [...selected.value];
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await CallLogs.retry(id);
        ok += 1;
      } catch {
        fail += 1;
      }
    }
    notify(`재처리 완료: 성공 ${ok}건 / 실패 ${fail}건`, fail ? 'warning' : 'success');
    await load();
  } finally {
    bulkBusy.value = false;
  }
}

function pretty(v: unknown): string {
  if (v === null || v === undefined) return '(없음)';
  return JSON.stringify(v, null, 2);
}

const responseHeaders = computed(() => {
  const h = (detail.value?.response as any)?.headers;
  return h && typeof h === 'object' ? h : null;
});

function openDetail(item: CallLogItem) {
  detail.value = item;
  detailDialog.value = true;
}

async function copyDetail() {
  if (!detail.value) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(detail.value, null, 2));
    notify('전체 JSON을 클립보드에 복사했습니다');
  } catch {
    notify('복사 실패', 'error');
  }
}

async function openChain(id: number) {
  try {
    chain.value = (await CallLogs.chain(id)).data;
    chainDialog.value = true;
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '체인 조회 실패', 'error');
  }
}

onMounted(load);
</script>

<style scoped>
/* 시간 필터 텍스트 input — 'YYYY-MM-DD HH:mm' 한 칸. 키보드만으로 빠른 입력. */
.dt-input {
  flex: 0 0 auto;
}
.dt-input-text {
  width: 200px;
}
.dt-input-text :deep(input) {
  font-variant-numeric: tabular-nums;
}

.json-block {
  background: #0e1116;
  color: #e6edf3;
  padding: 12px 14px;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12.5px;
  line-height: 1.45;
  overflow: auto;
  max-height: 320px;
  white-space: pre;
}
</style>