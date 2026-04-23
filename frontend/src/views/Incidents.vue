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

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Incidents, type IncidentItem } from '@/api/client';
import { formatDateTime } from '@/utils/format';

const headers = [
  { title: '상태', key: 'state', width: 110 },
  { title: '감지 시각', key: 'detected_at' },
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '유형', key: 'type' },
  { title: '심각도', key: 'severity' },
  { title: '요약', key: 'summary' },
  { title: '해결 시각', key: 'resolved_at' },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 160 },
];

const rows = ref<IncidentItem[]>([]);
const loading = ref(false);
const unresolvedOnly = ref(false);
const snack = reactive({ show: false, text: '', color: 'success' });

const unresolvedCount = computed(() => rows.value.filter((r) => !r.resolved_at).length);
const resolvedCount = computed(() => rows.value.filter((r) => !!r.resolved_at).length);

function sevColor(s: string) {
  return { critical: 'error', warning: 'warning', info: 'info' }[s] ?? 'grey';
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

onMounted(() => load());
</script>