<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">감사 로그</h2>
      <v-chip class="ml-3" size="small" color="grey" variant="tonal" prepend-icon="mdi-shield-lock-outline">
        Append-only · 변경/삭제 불가
      </v-chip>
      <v-spacer />
      <v-btn
        prepend-icon="mdi-microsoft-excel"
        color="success"
        variant="elevated"
        size="small"
        :href="excelHref"
        download
      >
        Excel 내보내기
      </v-btn>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      모든 변경 액션의 <strong>누가 · 언제 · 무엇을 · 어디서</strong> 영구 기록 (감사·사고 분석용).
      ADMIN / VIEWER 만 접근.
    </v-alert>

    <v-card class="mb-4">
      <v-card-text>
        <v-row dense align="center">
          <v-col cols="12" md="3">
            <v-text-field v-model="filter.actor" label="사용자명 (부분일치)" density="compact" variant="outlined" clearable hide-details />
          </v-col>
          <v-col cols="12" md="3">
            <v-autocomplete
              v-model="filter.action"
              :items="actionsList"
              label="액션"
              density="compact"
              variant="outlined"
              clearable
              hide-details
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-select
              v-model="filter.resource_type"
              :items="resourceTypesList"
              label="자원 종류"
              density="compact"
              variant="outlined"
              clearable
              hide-details
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-select
              v-model="rangeDays"
              :items="rangeOptions"
              item-title="label"
              item-value="value"
              label="기간"
              density="compact"
              variant="outlined"
              hide-details
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-btn block color="primary" @click="load" :loading="loading">검색</v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card>
      <v-data-table
        :headers="headers"
        :items="rows"
        :loading="loading"
        density="compact"
        :items-per-page="50"
      >
        <template #item.occurred_at="{ item }">
          <span class="text-caption">{{ formatDateTime(item.occurred_at) }}</span>
        </template>
        <template #item.actor_username="{ item }">
          <strong>{{ item.actor_username }}</strong>
          <v-chip
            v-if="item.actor_role"
            class="ml-1"
            size="x-small"
            :color="roleColor(item.actor_role)"
            variant="tonal"
          >
            {{ item.actor_role }}
          </v-chip>
        </template>
        <template #item.action="{ item }">
          <v-chip size="small" variant="tonal" :color="actionColor(item.action)">
            {{ item.action }}
          </v-chip>
        </template>
        <template #item.resource="{ item }">
          <span v-if="item.resource_type" class="text-caption">
            {{ item.resource_type }}{{ item.resource_id ? ' #' + item.resource_id : '' }}
          </span>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.changes="{ item }">
          <v-btn
            v-if="item.before_value || item.after_value"
            size="x-small"
            variant="tonal"
            color="info"
            prepend-icon="mdi-file-document-outline"
            @click="openDetail(item)"
          >
            변경 내역
          </v-btn>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.ip="{ item }">
          <span class="text-caption">{{ item.ip || '-' }}</span>
        </template>
      </v-data-table>
    </v-card>

    <!-- detail dialog -->
    <v-dialog v-model="detailDialog" max-width="900" scrollable>
      <v-card v-if="detail">
        <v-card-title class="d-flex align-center">
          <span>감사 로그 #{{ detail.id }}</span>
          <v-chip class="ml-3" size="small" :color="actionColor(detail.action)">{{ detail.action }}</v-chip>
        </v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="12" md="6">
              <div class="text-caption text-medium-emphasis">시각</div>
              <div>{{ formatDateTime(detail.occurred_at) }}</div>
            </v-col>
            <v-col cols="12" md="6">
              <div class="text-caption text-medium-emphasis">사용자</div>
              <div>
                <strong>{{ detail.actor_username }}</strong>
                <v-chip v-if="detail.actor_role" class="ml-2" size="x-small" :color="roleColor(detail.actor_role)">
                  {{ detail.actor_role }}
                </v-chip>
              </div>
            </v-col>
            <v-col v-if="detail.resource_type" cols="12" md="6">
              <div class="text-caption text-medium-emphasis">자원</div>
              <div>{{ detail.resource_type }} #{{ detail.resource_id }}</div>
            </v-col>
            <v-col cols="12" md="6">
              <div class="text-caption text-medium-emphasis">IP / User-Agent</div>
              <div class="text-caption">{{ detail.ip || '-' }} · {{ (detail.user_agent || '').slice(0, 60) }}</div>
            </v-col>
          </v-row>

          <v-divider class="my-3" />

          <div v-if="detail.before_value" class="mb-3">
            <div class="text-subtitle-2 mb-1">
              <v-icon icon="mdi-arrow-left-bold-outline" size="small" /> 변경 전
            </div>
            <pre class="json-block">{{ pretty(detail.before_value) }}</pre>
          </div>
          <div v-if="detail.after_value">
            <div class="text-subtitle-2 mb-1">
              <v-icon icon="mdi-arrow-right-bold-outline" size="small" /> 변경 후
            </div>
            <pre class="json-block">{{ pretty(detail.after_value) }}</pre>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="detailDialog = false">닫기</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { AuditLogs, type AuditLogItem } from '@/api/client';
import { formatDateTime } from '@/utils/format';

const headers = [
  { title: '시각', key: 'occurred_at', width: 170 },
  { title: '사용자', key: 'actor_username', width: 180 },
  { title: '액션', key: 'action', width: 200 },
  { title: '자원', key: 'resource', width: 180, sortable: false },
  { title: '변경 내역', key: 'changes', width: 130, sortable: false },
  { title: 'IP', key: 'ip', width: 120 },
];

const rangeOptions = [
  { label: '최근 1일', value: 1 },
  { label: '최근 7일', value: 7 },
  { label: '최근 30일', value: 30 },
  { label: '최근 90일', value: 90 },
  { label: '전체', value: 0 },
];

const rows = ref<AuditLogItem[]>([]);
const actionsList = ref<string[]>([]);
const resourceTypesList = ref<string[]>([]);
const loading = ref(false);
const rangeDays = ref(7);

const filter = reactive<{ actor: string; action: string | null; resource_type: string | null }>({
  actor: '',
  action: null,
  resource_type: null,
});

const detailDialog = ref(false);
const detail = ref<AuditLogItem | null>(null);

const params = computed(() => {
  const p: Record<string, unknown> = { limit: 500 };
  if (filter.actor) p.actor = filter.actor;
  if (filter.action) p.action = filter.action;
  if (filter.resource_type) p.resource_type = filter.resource_type;
  if (rangeDays.value > 0) {
    const since = new Date();
    since.setDate(since.getDate() - rangeDays.value);
    p.since = since.toISOString();
  }
  return p;
});

const excelHref = computed(() => {
  const exportParams = { ...params.value };
  delete exportParams.limit;
  return AuditLogs.exportXlsxUrl(exportParams);
});

function roleColor(r: string) {
  return ({ ADMIN: 'error', OPERATOR: 'warning', VIEWER: 'info' } as Record<string, string>)[r] ?? 'grey';
}

function actionColor(action: string) {
  if (action.startsWith('auth.login_failed')) return 'error';
  if (action.startsWith('auth.')) return 'info';
  if (action.includes('delete') || action.includes('archive')) return 'error';
  if (action.includes('execute') || action.includes('retry')) return 'warning';
  if (action.includes('create')) return 'success';
  if (action.includes('update') || action.includes('secret')) return 'orange';
  return 'grey';
}

function pretty(v: unknown): string {
  if (v === null || v === undefined) return '(없음)';
  return JSON.stringify(v, null, 2);
}

function openDetail(item: AuditLogItem) {
  detail.value = item;
  detailDialog.value = true;
}

async function load() {
  loading.value = true;
  try {
    rows.value = (await AuditLogs.list(params.value)).data;
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  const [a, r] = await Promise.all([AuditLogs.actions(), AuditLogs.resourceTypes()]);
  actionsList.value = a.data;
  resourceTypesList.value = r.data;
  await load();
});

watch(rangeDays, () => load());
</script>

<style scoped>
.json-block {
  background: #0e1116;
  color: #e6edf3;
  padding: 12px 14px;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.45;
  overflow: auto;
  max-height: 280px;
  white-space: pre;
}
</style>
