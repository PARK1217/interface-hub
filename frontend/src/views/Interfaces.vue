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
              :disabled="filter.trashOnly"
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-switch
              v-model="filter.trashOnly"
              label="휴지통"
              hide-details
              density="compact"
              color="grey"
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="1" class="d-flex align-center justify-end">
            <span class="text-caption text-medium-emphasis">{{ rows.length }}건</span>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card>
      <v-alert
        v-if="filter.trashOnly"
        type="info"
        variant="tonal"
        density="compact"
        class="mx-3 mt-3"
      >
        보관 처리된 인터페이스 입니다. 호출 로그·장애 이력·SLA 목표는
        <strong>감사 대응을 위해 영구 보존</strong>되며, 자동 삭제되지 않습니다.
        다시 사용하려면 우측 <strong>복원</strong> 버튼을 누르세요.
      </v-alert>
      <v-data-table
        :headers="headers"
        :items="rows"
        :loading="loading"
        item-value="id"
        density="comfortable"
        :row-props="rowProps"
      >
        <template #item.protocol="{ item }">
          <v-chip size="small" :color="protocolColor(item.protocol)">{{ item.protocol }}</v-chip>
        </template>
        <template #item.schedule_cron="{ item }">
          <v-chip
            size="small"
            variant="tonal"
            :color="cronToLabel(item.schedule_cron).color"
            :prepend-icon="cronToLabel(item.schedule_cron).icon"
          >
            {{ cronToLabel(item.schedule_cron).label }}
          </v-chip>
        </template>
        <template #item.enabled="{ item }">
          <v-chip
            v-if="item.deleted_at"
            size="small"
            color="grey"
            variant="tonal"
            prepend-icon="mdi-archive-outline"
          >
            보관됨
          </v-chip>
          <v-chip
            v-else-if="item.enabled"
            size="small"
            color="success"
            variant="tonal"
            prepend-icon="mdi-circle-medium"
          >
            활성
          </v-chip>
          <v-chip
            v-else
            size="small"
            color="warning"
            variant="tonal"
            prepend-icon="mdi-pause-circle-outline"
          >
            일시중지
          </v-chip>
        </template>
        <template #item.actions="{ item }">
          <template v-if="!item.deleted_at">
            <v-btn icon="mdi-play" variant="text" size="small" @click="run(item)" :loading="running === item.id" />
            <v-btn icon="mdi-pencil" variant="text" size="small" @click="openEdit(item)" />
            <v-btn icon="mdi-archive-arrow-down" variant="text" size="small" color="error" @click="remove(item)" />
          </template>
          <template v-else>
            <v-btn
              prepend-icon="mdi-restore"
              variant="flat"
              color="success"
              size="small"
              @click="restore(item)"
            >
              복원
            </v-btn>
          </template>
        </template>
      </v-data-table>
    </v-card>

    <v-dialog v-model="dialog" max-width="720" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center pa-4">
          <v-icon
            :icon="form.id ? 'mdi-pencil-outline' : 'mdi-plus-circle-outline'"
            class="mr-2"
            color="primary"
          />
          <span>{{ form.id ? '인터페이스 수정' : '새 인터페이스 등록' }}</span>
          <v-spacer />
          <v-btn
            v-if="form.id && !form.deleted_at"
            size="small"
            variant="text"
            color="error"
            prepend-icon="mdi-archive-arrow-down-outline"
            @click="archiveFromDialog"
          >
            보관 처리
          </v-btn>
        </v-card-title>
        <v-divider />
        <v-card-text class="pa-5">
          <div class="text-overline text-medium-emphasis mb-2">기본 정보</div>
          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.name" label="이름 *" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.organization" label="기관" density="comfortable" variant="outlined" />
            </v-col>
          </v-row>

          <div class="text-overline text-medium-emphasis mb-2 mt-3">연결</div>
          <v-row dense>
            <v-col cols="6" md="3">
              <v-select v-model="form.protocol" :items="['REST', 'SOAP', 'FTP', 'MQ', 'BATCH']" label="프로토콜" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="6" md="3">
              <v-select v-model="form.method" :items="['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" label="메서드" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.endpoint" label="엔드포인트 URL *" density="comfortable" variant="outlined" />
            </v-col>
          </v-row>

          <!-- 스케줄 입력 — raw cron 직접 입력 폼 절대 두지 말 것.
               운영자가 cron 문법 (* / - , 등) 잘못 쳐서 깨지는 사고가 표준
               이슈. 라디오 버튼으로 6가지 패턴 (사용 안 함/매 N분/매시간/매일/
               매주/매월) 선택 → 드롭다운으로 시·분·일·요일만 입력 → 내부에서
               cron 표현식 자동 생성. 운영자에게 cron 문법 노출 0. -->
          <div class="text-overline text-medium-emphasis mb-2 mt-4">스케줄</div>
          <v-card variant="outlined" rounded="lg" class="pa-4 mb-2">
            <v-btn-toggle
              v-model="sched.type"
              mandatory
              density="comfortable"
              color="primary"
              variant="outlined"
              divided
              class="mb-3 d-flex flex-wrap"
              style="row-gap: 4px"
            >
              <v-btn
                v-for="t in scheduleTypes"
                :key="t.value"
                :value="t.value"
                size="small"
              >
                {{ t.label }}
              </v-btn>
            </v-btn-toggle>

            <v-row v-if="sched.type === 'minutes'" dense>
              <v-col cols="12" md="6">
                <v-select
                  v-model="sched.everyN"
                  :items="[5, 10, 15, 20, 30]"
                  label="N분 마다"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <v-row v-if="sched.type === 'hourly'" dense>
              <v-col cols="12" md="6">
                <v-select
                  v-model="sched.minute"
                  :items="minuteChoices"
                  label="매시 N분"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <v-row v-if="['daily','weekly','monthly'].includes(sched.type)" dense>
              <v-col cols="6" md="3">
                <v-select
                  v-model="sched.hour"
                  :items="hourChoices"
                  label="시"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
              <v-col cols="6" md="3">
                <v-select
                  v-model="sched.minute"
                  :items="minuteChoices"
                  label="분"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
              <v-col v-if="sched.type === 'monthly'" cols="12" md="6">
                <v-select
                  v-model="sched.day"
                  :items="dayChoices"
                  label="매월 N일"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <div v-if="sched.type === 'weekly'" class="mt-3">
              <div class="text-caption text-medium-emphasis mb-2">요일 (복수 선택 가능)</div>
              <v-btn-toggle
                v-model="sched.weekdays"
                multiple
                color="primary"
                density="comfortable"
                variant="outlined"
                divided
              >
                <v-btn
                  v-for="d in weekdayChoices"
                  :key="d.value"
                  :value="d.value"
                  size="small"
                  :disabled="sched.weekdays.length === 1 && sched.weekdays[0] === d.value"
                >
                  {{ d.label }}
                </v-btn>
              </v-btn-toggle>
            </div>

            <v-divider class="my-3" />

            <div class="d-flex align-center flex-wrap" style="gap:8px">
              <v-chip
                v-if="cronPreview.valid && cronPreview.next_runs?.length"
                size="small"
                color="success"
                variant="tonal"
                prepend-icon="mdi-clock-check-outline"
              >
                다음 실행 ·
                {{ cronPreview.next_runs.map(formatDateTime).slice(0, 3).join('  ·  ') }}
              </v-chip>
              <v-chip
                v-else-if="sched.type !== 'none' && cronPreview.error"
                size="small"
                color="error"
                variant="tonal"
                prepend-icon="mdi-alert"
              >
                {{ cronPreview.error }}
              </v-chip>
              <v-chip
                v-if="sched.type === 'none'"
                size="small"
                variant="tonal"
                prepend-icon="mdi-hand-back-right-outline"
              >
                수동 실행만 가능 — ▶ 버튼 또는 API 로 직접 실행
              </v-chip>
            </div>
          </v-card>

          <div class="text-overline text-medium-emphasis mb-2 mt-4">인증</div>
          <v-row dense>
            <v-col cols="12" md="4">
              <v-select v-model="form.auth_type" :items="['NONE', 'BASIC', 'API_KEY', 'OAUTH2', 'BEARER']" label="유형" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="8">
              <v-text-field
                v-model="form.auth_secret"
                label="시크릿 (저장 시 AES-GCM 암호화)"
                type="password"
                density="comfortable"
                variant="outlined"
                prepend-inner-icon="mdi-lock-outline"
                :disabled="form.auth_type === 'NONE'"
                hint="기존 값을 바꾸지 않으려면 비워두세요"
                persistent-hint
              />
            </v-col>
          </v-row>

          <div class="text-overline text-medium-emphasis mb-2 mt-4">임계값 (선택)</div>
          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="form.response_ms_threshold"
                label="응답시간 임계값 (ms)"
                type="number"
                density="comfortable"
                variant="outlined"
                hint="이 값을 초과하면 SLOW_RESPONSE 장애 자동 감지"
                persistent-hint
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="form.failure_rate_threshold"
                label="실패율 임계값 (0~1)"
                type="number"
                step="0.05"
                density="comfortable"
                variant="outlined"
                hint="최근 15분 윈도우 실패율이 이 값 초과 시 자동 장애"
                persistent-hint
              />
            </v-col>
          </v-row>

          <v-divider class="my-4" />
          <div class="d-flex align-start">
            <v-switch
              v-model="form.enabled"
              color="primary"
              label="스케줄 실행 활성"
              hide-details
              density="comfortable"
              class="mt-0"
            />
            <div class="text-caption text-medium-emphasis ml-4 mt-2" style="max-width:430px">
              꺼두면 cron 자동 실행이 멈추지만, 행은 활성 목록에 그대로 보이고
              운영자가 ▶ 수동 실행은 가능합니다.
              <strong>영구히 사용하지 않을 인터페이스는 우상단의 "보관 처리"</strong>
              버튼을 사용하세요 (감사 이력은 영구 보존).
            </div>
          </div>
        </v-card-text>
        <v-divider />
        <v-card-actions class="pa-4">
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">취소</v-btn>
          <v-btn color="primary" variant="elevated" prepend-icon="mdi-content-save-outline" @click="save" :loading="saving">
            저장
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<style scoped>
/* Dim the deleted row's metadata, but keep the action cell crisp so the
   "복원" button stays clearly clickable. */
:deep(tr.row-deleted) {
  background: rgba(120, 120, 120, 0.05);
  box-shadow: inset 3px 0 0 0 rgba(120, 120, 120, 0.5);
}
:deep(tr.row-deleted td:not(:last-child)) {
  opacity: 0.5;
  filter: grayscale(0.4);
}
:deep(tr.row-deleted td:last-child) {
  opacity: 1;
}
</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { Interfaces, type InterfaceItem } from '@/api/client';
import { cronToLabel, formatDateTime } from '@/utils/format';

type ScheduleType = 'none' | 'minutes' | 'hourly' | 'daily' | 'weekly' | 'monthly';

const scheduleTypes: { value: ScheduleType; label: string }[] = [
  { value: 'none', label: '사용 안 함' },
  { value: 'minutes', label: '매 N분' },
  { value: 'hourly', label: '매시간' },
  { value: 'daily', label: '매일' },
  { value: 'weekly', label: '매주' },
  { value: 'monthly', label: '매월' },
];

const hourChoices = Array.from({ length: 24 }, (_, i) => ({
  value: i,
  title: `${String(i).padStart(2, '0')}시`,
}));
const minuteChoices = [0, 5, 10, 15, 20, 30, 45].map((m) => ({
  value: m,
  title: `${String(m).padStart(2, '0')}분`,
}));
const dayChoices = Array.from({ length: 28 }, (_, i) => ({
  value: i + 1,
  title: `${i + 1}일`,
}));
// APScheduler CronTrigger uses 0=Sun..6=Sat; cron standard same
const weekdayChoices = [
  { value: 1, label: '월' },
  { value: 2, label: '화' },
  { value: 3, label: '수' },
  { value: 4, label: '목' },
  { value: 5, label: '금' },
  { value: 6, label: '토' },
  { value: 0, label: '일' },
];

const protocolOptions = ['REST', 'SOAP', 'FTP', 'MQ', 'BATCH'];

const filter = reactive<{
  protocol: string | null;
  organization: string | null;
  enabledOnly: boolean;
  trashOnly: boolean;
}>({
  protocol: null,
  organization: null,
  enabledOnly: false,
  trashOnly: false,
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
  { title: '상태', key: 'enabled', width: 110 },
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

const sched = reactive<{
  type: ScheduleType;
  everyN: number;
  hour: number;
  minute: number;
  weekdays: number[];
  day: number;
}>({
  type: 'none',
  everyN: 10,
  hour: 9,
  minute: 0,
  weekdays: [1, 2, 3, 4, 5],
  day: 1,
});

function buildCron(): string {
  switch (sched.type) {
    case 'none':
      return '';
    case 'minutes':
      return `*/${sched.everyN} * * * *`;
    case 'hourly':
      return `${sched.minute} * * * *`;
    case 'daily':
      return `${sched.minute} ${sched.hour} * * *`;
    case 'weekly': {
      const wds = sched.weekdays.length ? [...sched.weekdays].sort((a, b) => a - b) : [1];
      return `${sched.minute} ${sched.hour} * * ${wds.join(',')}`;
    }
    case 'monthly':
      return `${sched.minute} ${sched.hour} ${sched.day} * *`;
  }
}

function parseCron(expr: string | null | undefined) {
  // Reset to defaults first
  Object.assign(sched, {
    type: 'none' as ScheduleType,
    everyN: 10,
    hour: 9,
    minute: 0,
    weekdays: [1, 2, 3, 4, 5],
    day: 1,
  });
  if (!expr) return;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return; // unsupported custom expression
  const [m, h, d, mo, w] = parts;

  if (mo === '*' && d === '*' && w === '*' && h === '*' && m.startsWith('*/')) {
    sched.type = 'minutes';
    sched.everyN = parseInt(m.slice(2)) || 10;
    return;
  }
  if (mo === '*' && d === '*' && w === '*' && h === '*' && /^\d+$/.test(m)) {
    sched.type = 'hourly';
    sched.minute = parseInt(m);
    return;
  }
  if (mo === '*' && d === '*' && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'daily';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    return;
  }
  if (mo === '*' && d === '*' && w !== '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'weekly';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    const out: number[] = [];
    for (const tok of w.split(',')) {
      if (tok.includes('-')) {
        const [a, b] = tok.split('-').map((n) => parseInt(n));
        for (let i = a; i <= b; i++) out.push(i);
      } else {
        const n = parseInt(tok);
        if (!Number.isNaN(n)) out.push(n);
      }
    }
    sched.weekdays = out.length ? out : [1];
    return;
  }
  if (mo === '*' && /^\d+$/.test(d) && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'monthly';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    sched.day = parseInt(d);
    return;
  }
  // Unknown pattern — leave as 'none' but preserve raw via form.schedule_cron
}

const cronPreview = reactive<{ valid: boolean; error?: string; next_runs?: string[] }>({
  valid: false,
});
let cronTimer: ReturnType<typeof setTimeout> | null = null;

// Whenever the structured selector changes, rebuild cron → update form
watch(
  sched,
  () => {
    form.schedule_cron = buildCron() || null;
  },
  { deep: true },
);

// Preview on cron change
watch(
  () => form.schedule_cron,
  (expr) => {
    if (cronTimer) clearTimeout(cronTimer);
    if (!expr) {
      Object.assign(cronPreview, { valid: false, error: undefined, next_runs: [] });
      return;
    }
    cronTimer = setTimeout(async () => {
      try {
        const res = await Interfaces.cronPreview(expr.trim());
        Object.assign(cronPreview, res.data);
      } catch {
        Object.assign(cronPreview, { valid: false, error: 'preview 실패' });
      }
    }, 200);
  },
);

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
    if (filter.trashOnly) {
      params.only_deleted = true;
    } else if (filter.enabledOnly) {
      params.enabled = true;
    }
    rows.value = (await Interfaces.list(params)).data;
  } finally {
    loading.value = false;
  }
}

function rowProps({ item }: { item: InterfaceItem }) {
  return item.deleted_at ? { class: 'row-deleted' } : {};
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
  parseCron('');
  dialog.value = true;
}

function openEdit(item: InterfaceItem) {
  Object.assign(form, item, { auth_secret: '' });
  parseCron(item.schedule_cron ?? '');
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
  const msg =
    `'${item.name}' 을(를) 보관 처리할까요?\n\n` +
    `· 호출 로그·장애 이력·SLA 목표는 감사 대응을 위해 영구 보존됩니다.\n` +
    `· 스케줄 실행은 즉시 중지됩니다.\n` +
    `· 휴지통에서 언제든 복원할 수 있습니다.\n\n` +
    `※ 잠시만 멈출 거면 행을 클릭해 "스케줄 실행" 토글을 끄는 것이 적절합니다.`;
  if (!confirm(msg)) return;
  await Interfaces.remove(item.id);
  notify('보관 처리됨 — 휴지통에서 복원 가능');
  await load();
}

async function archiveFromDialog() {
  if (!form.id) return;
  const target = rows.value.find((r) => r.id === form.id);
  if (!target) return;
  await remove(target);
  dialog.value = false;
}

async function restore(item: InterfaceItem) {
  await Interfaces.restore(item.id);
  notify(`${item.name} 복원됨`);
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
