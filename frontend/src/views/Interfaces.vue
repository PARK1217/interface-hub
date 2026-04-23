<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">인터페이스 관리</h2>
      <v-spacer />
      <v-btn color="primary" prepend-icon="mdi-plus" @click="openCreate">새 인터페이스</v-btn>
    </div>

    <v-card class="mb-4">
      <v-card-text>
        <v-row dense>
          <v-col cols="12" md="3">
            <v-select
              v-model="filter.protocol"
              :items="protocolOptions"
              label="프로토콜"
              clearable
              density="compact"
              hide-details
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="4">
            <v-select
              v-model="filter.organization"
              :items="organizationOptions"
              label="기관"
              clearable
              density="compact"
              hide-details
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-switch
              v-model="filter.enabledOnly"
              label="활성만"
              hide-details
              density="compact"
              color="primary"
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="3" class="d-flex align-center justify-end">
            <span class="text-caption text-medium-emphasis">{{ rows.length }}건</span>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card>
      <v-data-table
        :headers="headers"
        :items="rows"
        :loading="loading"
        item-value="id"
        density="comfortable"
      >
        <template #item.protocol="{ item }">
          <v-chip size="small" :color="protocolColor(item.protocol)">{{ item.protocol }}</v-chip>
        </template>
        <template #item.enabled="{ item }">
          <v-icon :color="item.enabled ? 'success' : 'grey'" :icon="item.enabled ? 'mdi-check' : 'mdi-minus'" />
        </template>
        <template #item.actions="{ item }">
          <v-btn icon="mdi-play" variant="text" size="small" @click="run(item)" :loading="running === item.id" />
          <v-btn icon="mdi-pencil" variant="text" size="small" @click="openEdit(item)" />
          <v-btn icon="mdi-delete" variant="text" size="small" @click="remove(item)" />
        </template>
      </v-data-table>
    </v-card>

    <v-dialog v-model="dialog" max-width="640">
      <v-card>
        <v-card-title>{{ form.id ? '인터페이스 수정' : '새 인터페이스' }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" label="이름 *" />
          <v-text-field v-model="form.organization" label="기관" />
          <v-row>
            <v-col cols="6">
              <v-select v-model="form.protocol" :items="['REST', 'SOAP', 'FTP', 'MQ', 'BATCH']" label="프로토콜" />
            </v-col>
            <v-col cols="6">
              <v-select v-model="form.method" :items="['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" label="메서드" />
            </v-col>
          </v-row>
          <v-text-field v-model="form.endpoint" label="엔드포인트 URL *" />
          <v-text-field v-model="form.schedule_cron" label="Cron (선택, 예: */5 * * * *)" />
          <v-row>
            <v-col cols="6">
              <v-select v-model="form.auth_type" :items="['NONE', 'BASIC', 'API_KEY', 'OAUTH2', 'BEARER']" label="인증" />
            </v-col>
            <v-col cols="6">
              <v-text-field v-model="form.auth_secret" label="인증 시크릿 (저장 시 AES-GCM 암호화)" type="password" />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="6">
              <v-text-field v-model.number="form.response_ms_threshold" label="응답시간 임계값(ms)" type="number" />
            </v-col>
            <v-col cols="6">
              <v-text-field v-model.number="form.failure_rate_threshold" label="실패율 임계값(0~1)" type="number" step="0.05" />
            </v-col>
          </v-row>
          <v-switch v-model="form.enabled" color="primary" label="활성화" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="dialog = false">취소</v-btn>
          <v-btn color="primary" @click="save" :loading="saving">저장</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Interfaces, type InterfaceItem } from '@/api/client';

const protocolOptions = ['REST', 'SOAP', 'FTP', 'MQ', 'BATCH'];

const filter = reactive<{ protocol: string | null; organization: string | null; enabledOnly: boolean }>({
  protocol: null,
  organization: null,
  enabledOnly: false,
});

const organizationOptions = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.organization).filter(Boolean))) as string[],
);

const headers = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '이름', key: 'name' },
  { title: '기관', key: 'organization' },
  { title: '프로토콜', key: 'protocol' },
  { title: '엔드포인트', key: 'endpoint' },
  { title: '스케줄', key: 'schedule_cron' },
  { title: '활성', key: 'enabled', width: 80 },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 160 },
];

const rows = ref<InterfaceItem[]>([]);
const loading = ref(false);
const saving = ref(false);
const running = ref<number | null>(null);
const dialog = ref(false);
const snack = reactive({ show: false, text: '', color: 'success' });

const form = reactive<Partial<InterfaceItem> & { auth_secret?: string }>({
  protocol: 'REST',
  method: 'GET',
  auth_type: 'NONE',
  enabled: true,
});

function protocolColor(p: string) {
  return { REST: 'primary', SOAP: 'secondary', FTP: 'warning', MQ: 'success', BATCH: 'purple' }[p] ?? 'grey';
}

function notify(text: string, color = 'success') {
  Object.assign(snack, { show: true, text, color });
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, unknown> = {};
    if (filter.protocol) params.protocol = filter.protocol;
    if (filter.organization) params.organization = filter.organization;
    if (filter.enabledOnly) params.enabled = true;
    rows.value = (await Interfaces.list(params)).data;
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, {
    id: undefined,
    name: '',
    organization: '',
    protocol: 'REST',
    method: 'GET',
    endpoint: '',
    schedule_cron: '',
    auth_type: 'NONE',
    auth_secret: '',
    response_ms_threshold: null,
    failure_rate_threshold: null,
    enabled: true,
  });
  dialog.value = true;
}

function openEdit(item: InterfaceItem) {
  Object.assign(form, item, { auth_secret: '' });
  dialog.value = true;
}

async function save() {
  saving.value = true;
  try {
    const payload = { ...form };
    if (!payload.auth_secret) delete payload.auth_secret;
    if (form.id) await Interfaces.update(form.id, payload);
    else await Interfaces.create(payload);
    notify('저장 완료');
    dialog.value = false;
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '저장 실패', 'error');
  } finally {
    saving.value = false;
  }
}

async function remove(item: InterfaceItem) {
  if (!confirm(`'${item.name}' 을(를) 삭제할까요?`)) return;
  await Interfaces.remove(item.id);
  notify('삭제됨');
  await load();
}

async function run(item: InterfaceItem) {
  running.value = item.id;
  try {
    const res = await Interfaces.execute(item.id);
    notify(`${item.name} → ${res.data.status} (${res.data.duration_ms}ms)`,
      res.data.status === 'SUCCESS' ? 'success' : 'warning');
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '실행 실패', 'error');
  } finally {
    running.value = null;
  }
}

onMounted(load);
</script>
