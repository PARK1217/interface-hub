<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">SLA 리포트</h2>
      <v-spacer />
      <v-select v-model="days" :items="[7, 14, 30, 90]" label="기간(일)" density="compact" hide-details style="max-width:140px" @update:model-value="load" />
    </div>

    <v-card>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Sla, type SlaReportRow } from '@/api/client';

const headers = [
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '이름', key: 'interface_name' },
  { title: '가동률 (Uptime)', key: 'uptime_pct' },
  { title: '평균 응답', key: 'avg_response_ms' },
];

const rows = ref<SlaReportRow[]>([]);
const days = ref(30);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    rows.value = (await Sla.report(days.value)).data;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>