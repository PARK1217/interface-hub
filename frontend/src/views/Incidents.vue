<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">장애 이력</h2>
      <v-spacer />
      <v-switch v-model="unresolvedOnly" hide-details color="primary" label="미해결만" @update:model-value="load" />
    </div>
    <v-card>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
        <template #item.severity="{ item }">
          <v-chip size="small" :color="sevColor(item.severity)">{{ item.severity }}</v-chip>
        </template>
        <template #item.type="{ item }">
          <v-chip size="small" variant="outlined">{{ item.type }}</v-chip>
        </template>
        <template #item.resolved_at="{ item }">
          <span v-if="item.resolved_at">{{ item.resolved_at }}</span>
          <v-btn v-else color="primary" size="small" variant="text" @click="resolve(item.id)">해결 처리</v-btn>
        </template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Incidents, type IncidentItem } from '@/api/client';

const headers = [
  { title: '감지', key: 'detected_at' },
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '유형', key: 'type' },
  { title: '심각도', key: 'severity' },
  { title: '요약', key: 'summary' },
  { title: '해결', key: 'resolved_at' },
];

const rows = ref<IncidentItem[]>([]);
const loading = ref(false);
const unresolvedOnly = ref(false);

function sevColor(s: string) {
  return { critical: 'error', warning: 'warning', info: 'info' }[s] ?? 'grey';
}

async function load() {
  loading.value = true;
  try {
    rows.value = (await Incidents.list({ unresolved_only: unresolvedOnly.value })).data;
  } finally {
    loading.value = false;
  }
}

async function resolve(id: number) {
  await Incidents.resolve(id, '운영자 수동 해결 처리');
  await load();
}

onMounted(load);
</script>