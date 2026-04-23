<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">호출 로그 · 재처리</h2>
      <v-spacer />
      <v-chip v-if="selected.length" color="warning" variant="flat" class="mr-3">
        {{ selected.length }}건 선택
      </v-chip>
      <v-btn
        v-if="selected.length"
        color="warning"
        prepend-icon="mdi-restart"
        :loading="bulkBusy"
        @click="bulkRetry"
      >
        선택 일괄 재처리
      </v-btn>
    </div>

    <v-card class="mb-4">
      <v-card-text>
        <v-row>
          <v-col cols="12" md="2">
            <v-text-field v-model.number="filter.interface_id" label="인터페이스 ID" type="number" clearable density="compact" />
          </v-col>
          <v-col cols="12" md="2">
            <v-select v-model="filter.status" :items="statuses" label="상태" clearable density="compact" />
          </v-col>
          <v-col cols="12" md="3">
            <v-text-field v-model="filter.keyword" label="에러 메시지 키워드" clearable density="compact" />
          </v-col>
          <v-col cols="12" md="3">
            <v-switch v-model="filter.failedOnly" hide-details color="warning" label="실패만 (재처리 가능)" density="compact" />
          </v-col>
          <v-col cols="12" md="2" class="d-flex align-center">
            <v-btn block color="primary" @click="load" :loading="loading">검색</v-btn>
          </v-col>
        </v-row>
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
        </template>
        <template #item.actions="{ item }">
          <v-btn
            icon="mdi-eye-outline"
            size="x-small"
            variant="text"
            color="primary"
            title="상세 보기"
            @click="openDetail(item)"
          />
          <v-btn
            v-if="canRetry(item)"
            icon="mdi-restart"
            size="x-small"
            variant="text"
            :loading="retrying === item.id"
            color="warning"
            title="재실행"
            @click="retryOne(item)"
          />
          <v-btn
            v-if="item.parent_log_id || item.is_reprocessed"
            icon="mdi-source-branch"
            size="x-small"
            variant="text"
            color="info"
            title="재처리 체인 보기"
            @click="openChain(item.id)"
          />
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
              <div>{{ detail.triggered_by }}</div>
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

const statuses = ['SUCCESS', 'FAILURE', 'TIMEOUT', 'AUTH_ERROR', 'FORMAT_ERROR', 'SERVER_ERROR'];
const headers = [
  { title: '시각', key: 'called_at' },
  { title: 'IF', key: 'interface_id', width: 50 },
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
  keyword: string;
  failedOnly: boolean;
}>({ interface_id: null, status: null, keyword: '', failedOnly: false });

function notify(text: string, color = 'success') {
  Object.assign(snack, { show: true, text, color });
}

function canRetry(item: CallLogItem) {
  return item.status !== 'SUCCESS' && !item.is_reprocessed;
}

async function load() {
  loading.value = true;
  selected.value = [];
  try {
    const params: Record<string, unknown> = { limit: 200 };
    if (filter.interface_id) params.interface_id = filter.interface_id;
    if (filter.status) params.status = filter.status;
    if (filter.keyword) params.keyword = filter.keyword;
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