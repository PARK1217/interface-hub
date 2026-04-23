<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">장애 이력</h2>
      <v-spacer />
      <v-chip class="mr-3" size="small" color="error" variant="tonal">미해결 {{ unresolvedCount }}</v-chip>
      <v-chip class="mr-3" size="small" color="success" variant="tonal">해결 {{ resolvedCount }}</v-chip>
      <v-switch
        v-model="unresolvedOnly"
        hide-details
        color="error"
        label="미해결만 보기"
        density="compact"
        @update:model-value="(v) => load(!!v)"
      />
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      같은 원인으로 반복되는 호출 실패는 <strong>하나의 장애</strong>로 묶여 표시됩니다.
      매 실패마다 알림이 울리는 대신 한 번만 인지하면 되므로, 야간·휴일에도
      알림 폭주 없이 즉시 대응할 수 있습니다. 우측 <strong>[N건]</strong> 칩을 누르면
      이 장애를 일으킨 호출들을 시간순으로 확인할 수 있어요.
    </v-alert>

    <v-card>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
        <template #item.state="{ item }">
          <v-chip
            v-if="item.resolved_at"
            size="small"
            color="success"
            variant="flat"
            prepend-icon="mdi-check"
          >
            해결됨
          </v-chip>
          <v-chip v-else size="small" color="error" variant="flat" prepend-icon="mdi-alert">
            미해결
          </v-chip>
        </template>
        <template #item.severity="{ item }">
          <v-chip size="small" :color="sevColor(item.severity)">{{ item.severity }}</v-chip>
        </template>
        <template #item.type="{ item }">
          <v-chip size="small" variant="outlined">{{ item.type }}</v-chip>
        </template>
        <template #item.detected_at="{ item }">
          <span class="text-caption">{{ fmt(item.detected_at) }}</span>
        </template>
        <template #item.resolved_at="{ item }">
          <span v-if="item.resolved_at" class="text-caption">{{ fmt(item.resolved_at) }}</span>
          <span v-else class="text-caption text-medium-emphasis">—</span>
        </template>
        <template #item.related_log_count="{ item }">
          <v-chip
            v-if="(item.related_log_count ?? 0) > 0"
            size="small"
            color="info"
            variant="tonal"
            prepend-icon="mdi-file-document-multiple-outline"
            style="cursor: pointer"
            @click="openRelated(item)"
          >
            {{ item.related_log_count }}건
          </v-chip>
          <span v-else class="text-caption text-medium-emphasis">0건</span>
        </template>
        <template #item.actions="{ item }">
          <v-btn
            v-if="!item.resolved_at"
            color="success"
            size="small"
            variant="tonal"
            prepend-icon="mdi-check-bold"
            @click="resolve(item.id)"
          >
            해결로 표시
          </v-btn>
        </template>
      </v-data-table>
    </v-card>

    <!-- Related logs dialog -->
    <v-dialog v-model="relatedDialog" max-width="1100" scrollable>
      <v-card v-if="relatedTarget">
        <v-card-title class="d-flex align-center">
          <span>관련 호출 로그</span>
          <v-chip class="ml-3" size="small" :color="sevColor(relatedTarget.severity)">{{ relatedTarget.severity }}</v-chip>
          <v-chip class="ml-2" size="small" variant="outlined">{{ relatedTarget.type }}</v-chip>
          <v-chip class="ml-2" size="small" color="info">incident #{{ relatedTarget.id }}</v-chip>
          <v-spacer />
          <v-chip variant="tonal" size="small">{{ relatedLogs.length }}건</v-chip>
        </v-card-title>
        <v-card-subtitle>{{ relatedTarget.summary }}</v-card-subtitle>
        <v-card-text>
          <v-data-table
            :headers="relatedHeaders"
            :items="relatedLogs"
            :loading="relatedLoading"
            density="compact"
            :items-per-page="20"
          >
            <template #item.called_at="{ item }">
              <span class="text-caption">{{ fmt(item.called_at) }}</span>
            </template>
            <template #item.status="{ item }">
              <v-chip size="x-small" :color="item.status === 'SUCCESS' ? 'success' : 'error'">{{ item.status }}</v-chip>
            </template>
            <template #item.duration_ms="{ item }">{{ item.duration_ms }} ms</template>
          </v-data-table>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="relatedDialog = false">닫기</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Incidents, type CallLogItem, type IncidentItem } from '@/api/client';
import { formatDateTime } from '@/utils/format';

const headers = [
  { title: '상태', key: 'state', width: 110 },
  { title: '감지 시각', key: 'detected_at' },
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '유형', key: 'type' },
  { title: '심각도', key: 'severity' },
  { title: '요약', key: 'summary' },
  { title: '관련 호출', key: 'related_log_count', width: 110 },
  { title: '해결 시각', key: 'resolved_at' },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 160 },
];

const relatedHeaders = [
  { title: '시각', key: 'called_at' },
  { title: '#', key: 'id', width: 80 },
  { title: '상태', key: 'status', width: 100 },
  { title: 'HTTP', key: 'http_status', width: 80 },
  { title: '소요', key: 'duration_ms', width: 80 },
  { title: '에러', key: 'error_message' },
];

const rows = ref<IncidentItem[]>([]);
const loading = ref(false);
const unresolvedOnly = ref(false);
const snack = reactive({ show: false, text: '', color: 'success' });

const relatedDialog = ref(false);
const relatedTarget = ref<IncidentItem | null>(null);
const relatedLogs = ref<CallLogItem[]>([]);
const relatedLoading = ref(false);

const unresolvedCount = computed(() => rows.value.filter((r) => !r.resolved_at).length);
const resolvedCount = computed(() => rows.value.filter((r) => !!r.resolved_at).length);

function sevColor(s: string) {
  return ({ critical: 'error', warning: 'warning', info: 'info' } as Record<string, string>)[s] ?? 'grey';
}

const fmt = formatDateTime;

async function load(only?: boolean) {
  if (only !== undefined) unresolvedOnly.value = only;
  loading.value = true;
  try {
    rows.value = (await Incidents.list({ unresolved_only: unresolvedOnly.value })).data;
  } finally {
    loading.value = false;
  }
}

async function resolve(id: number) {
  await Incidents.resolve(id, '운영자 수동 해결 처리');
  Object.assign(snack, { show: true, text: `#${id} 해결로 표시됨`, color: 'success' });
  await load();
}

async function openRelated(incident: IncidentItem) {
  relatedTarget.value = incident;
  relatedDialog.value = true;
  relatedLoading.value = true;
  try {
    relatedLogs.value = (await Incidents.relatedLogs(incident.id)).data;
  } finally {
    relatedLoading.value = false;
  }
}

onMounted(() => load());
</script>