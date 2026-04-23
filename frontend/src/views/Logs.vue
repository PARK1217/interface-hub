<template>
  <div>
    <h2 class="text-h5 mb-4">호출 로그</h2>
    <v-card class="mb-4">
      <v-card-text>
        <v-row>
          <v-col cols="12" md="3"><v-text-field v-model.number="filter.interface_id" label="인터페이스 ID" type="number" clearable /></v-col>
          <v-col cols="12" md="3"><v-select v-model="filter.status" :items="statuses" label="상태" clearable /></v-col>
          <v-col cols="12" md="4"><v-text-field v-model="filter.keyword" label="에러 메시지 키워드" clearable /></v-col>
          <v-col cols="12" md="2" class="d-flex align-center">
            <v-btn block color="primary" @click="load">검색</v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
        <template #item.status="{ item }">
          <v-chip size="small" :color="item.status === 'SUCCESS' ? 'success' : 'error'">{{ item.status }}</v-chip>
        </template>
        <template #item.duration_ms="{ item }">{{ item.duration_ms }} ms</template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { CallLogs, type CallLogItem } from '@/api/client';

const statuses = ['SUCCESS', 'FAILURE', 'TIMEOUT', 'AUTH_ERROR', 'FORMAT_ERROR', 'SERVER_ERROR'];
const headers = [
  { title: '시각', key: 'called_at' },
  { title: 'IF', key: 'interface_id', width: 60 },
  { title: '상태', key: 'status' },
  { title: 'HTTP', key: 'http_status', width: 80 },
  { title: '소요', key: 'duration_ms', width: 100 },
  { title: '트리거', key: 'triggered_by', width: 100 },
  { title: '에러', key: 'error_message' },
];

const rows = ref<CallLogItem[]>([]);
const loading = ref(false);
const filter = reactive<{ interface_id: number | null; status: string | null; keyword: string }>({
  interface_id: null,
  status: null,
  keyword: '',
});

async function load() {
  loading.value = true;
  try {
    const params: Record<string, unknown> = { limit: 200 };
    if (filter.interface_id) params.interface_id = filter.interface_id;
    if (filter.status) params.status = filter.status;
    if (filter.keyword) params.keyword = filter.keyword;
    rows.value = (await CallLogs.search(params)).data;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>
