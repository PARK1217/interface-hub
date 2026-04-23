<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">사용자 관리</h2>
      <v-chip class="ml-3" size="small" color="error" variant="tonal" prepend-icon="mdi-shield-crown-outline">
        ADMIN 전용
      </v-chip>
      <v-spacer />
      <v-btn color="primary" prepend-icon="mdi-account-plus-outline" @click="openCreate">
        사용자 추가
      </v-btn>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      신규 사용자 생성·비밀번호 초기화·비활성화·권한 변경 모두 감사 로그에 자동 기록.
      자기 자신의 권한 변경 / 자기 계정 비활성화는 lockout 방지를 위해 차단됩니다.
    </v-alert>

    <v-card>
      <v-data-table :headers="headers" :items="rows" :loading="loading" density="comfortable">
        <template #item.role="{ item }">
          <v-chip size="small" :color="roleColor(item.role)">{{ item.role }}</v-chip>
        </template>
        <template #item.status="{ item }">
          <v-chip
            v-if="item.disabled_at"
            size="small"
            color="grey"
            variant="tonal"
            prepend-icon="mdi-account-off-outline"
          >
            비활성
          </v-chip>
          <v-chip
            v-else
            size="small"
            color="success"
            variant="tonal"
            prepend-icon="mdi-check-circle-outline"
          >
            활성
          </v-chip>
        </template>
        <template #item.last_login_at="{ item }">
          <span class="text-caption">{{ formatDateTime(item.last_login_at) || '-' }}</span>
        </template>
        <template #item.actions="{ item }">
          <v-btn
            icon="mdi-pencil"
            size="x-small"
            variant="text"
            title="권한·정보 수정"
            @click="openEdit(item)"
          />
          <v-btn
            icon="mdi-key-variant"
            size="x-small"
            variant="text"
            color="warning"
            title="비밀번호 초기화"
            @click="resetPassword(item)"
          />
          <v-btn
            v-if="!item.disabled_at"
            icon="mdi-account-off-outline"
            size="x-small"
            variant="text"
            color="error"
            :disabled="item.id === auth.user?.id"
            title="비활성화"
            @click="toggleEnable(item, false)"
          />
          <v-btn
            v-else
            icon="mdi-account-check-outline"
            size="x-small"
            variant="text"
            color="success"
            title="활성화"
            @click="toggleEnable(item, true)"
          />
        </template>
      </v-data-table>
    </v-card>

    <!-- create dialog -->
    <v-dialog v-model="createDialog" max-width="520">
      <v-card>
        <v-card-title>새 사용자 추가</v-card-title>
        <v-card-text>
          <v-text-field v-model="newUser.username" label="사용자명 *" density="comfortable" variant="outlined" />
          <v-text-field v-model="newUser.full_name" label="이름" density="comfortable" variant="outlined" />
          <v-text-field v-model="newUser.email" label="이메일" density="comfortable" variant="outlined" />
          <v-select
            v-model="newUser.role"
            :items="roles"
            label="역할"
            density="comfortable"
            variant="outlined"
          />
          <v-text-field
            v-model="newUser.password"
            label="초기 비밀번호 *"
            type="password"
            density="comfortable"
            variant="outlined"
            hint="사용자 첫 로그인 후 변경 권장"
            persistent-hint
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="createDialog = false">취소</v-btn>
          <v-btn color="primary" @click="submitCreate" :loading="busy">추가</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- edit dialog -->
    <v-dialog v-model="editDialog" max-width="520">
      <v-card v-if="editForm">
        <v-card-title>사용자 수정 — {{ editForm.username }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="editForm.full_name" label="이름" density="comfortable" variant="outlined" />
          <v-text-field v-model="editForm.email" label="이메일" density="comfortable" variant="outlined" />
          <v-select
            v-model="editForm.role"
            :items="roles"
            label="역할"
            density="comfortable"
            variant="outlined"
            :disabled="editForm.id === auth.user?.id"
            :hint="editForm.id === auth.user?.id ? '자기 자신의 권한은 변경 불가 (lockout 방지)' : ''"
            persistent-hint
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="editDialog = false">취소</v-btn>
          <v-btn color="primary" @click="submitEdit" :loading="busy">저장</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- temp password display dialog -->
    <v-dialog v-model="tempPwDialog" max-width="500">
      <v-card v-if="tempPw">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-key-variant" color="warning" class="mr-2" />
          임시 비밀번호 발급됨
        </v-card-title>
        <v-card-text>
          <v-alert type="warning" variant="tonal" density="compact" class="mb-3">
            <strong>이 화면을 닫으면 다시 볼 수 없습니다.</strong> 사용자에게 즉시 전달하고
            첫 로그인 후 변경하도록 안내하세요.
          </v-alert>
          <div class="text-caption text-medium-emphasis">사용자명</div>
          <div class="mb-3"><strong>{{ tempPw.username }}</strong></div>
          <div class="text-caption text-medium-emphasis">임시 비밀번호</div>
          <v-text-field
            :model-value="tempPw.temp_password"
            readonly
            variant="outlined"
            density="comfortable"
            append-inner-icon="mdi-content-copy"
            @click:append-inner="copyTemp"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="primary" @click="tempPwDialog = false">확인</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { Users, type UserItem } from '@/api/client';
import { formatDateTime } from '@/utils/format';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();

const roles = ['VIEWER', 'OPERATOR', 'ADMIN'];

const headers = [
  { title: '#', key: 'id', width: 60 },
  { title: '사용자명', key: 'username' },
  { title: '이름', key: 'full_name' },
  { title: '이메일', key: 'email' },
  { title: '역할', key: 'role', width: 110 },
  { title: '상태', key: 'status', width: 100 },
  { title: '마지막 로그인', key: 'last_login_at', width: 170 },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 160 },
];

const rows = ref<UserItem[]>([]);
const loading = ref(false);
const busy = ref(false);
const createDialog = ref(false);
const editDialog = ref(false);
const tempPwDialog = ref(false);
const tempPw = ref<{ user_id: number; username: string; temp_password: string } | null>(null);

const newUser = reactive({
  username: '',
  password: '',
  full_name: '',
  email: '',
  role: 'VIEWER',
});

const editForm = ref<UserItem | null>(null);

const snack = reactive({ show: false, text: '', color: 'success' });

function notify(text: string, color = 'success') {
  Object.assign(snack, { show: true, text, color });
}

function roleColor(r: string) {
  return ({ ADMIN: 'error', OPERATOR: 'warning', VIEWER: 'info' } as Record<string, string>)[r] ?? 'grey';
}

async function load() {
  loading.value = true;
  try {
    rows.value = (await Users.list()).data;
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(newUser, { username: '', password: '', full_name: '', email: '', role: 'VIEWER' });
  createDialog.value = true;
}

async function submitCreate() {
  if (!newUser.username || !newUser.password) {
    notify('사용자명·비밀번호 필수', 'error');
    return;
  }
  busy.value = true;
  try {
    await Users.create({ ...newUser });
    notify(`${newUser.username} 추가됨`);
    createDialog.value = false;
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '추가 실패', 'error');
  } finally {
    busy.value = false;
  }
}

function openEdit(item: UserItem) {
  editForm.value = { ...item };
  editDialog.value = true;
}

async function submitEdit() {
  if (!editForm.value) return;
  busy.value = true;
  try {
    await Users.update(editForm.value.id, {
      full_name: editForm.value.full_name ?? undefined,
      email: editForm.value.email ?? undefined,
      role: editForm.value.role,
    });
    notify(`${editForm.value.username} 수정됨`);
    editDialog.value = false;
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '수정 실패', 'error');
  } finally {
    busy.value = false;
  }
}

async function toggleEnable(item: UserItem, enable: boolean) {
  const verb = enable ? '활성화' : '비활성화';
  if (!confirm(`'${item.username}' 을(를) ${verb}할까요?`)) return;
  try {
    if (enable) await Users.enable(item.id);
    else await Users.disable(item.id);
    notify(`${item.username} ${verb}됨`);
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? `${verb} 실패`, 'error');
  }
}

async function resetPassword(item: UserItem) {
  if (!confirm(`'${item.username}' 의 비밀번호를 임시 비밀번호로 초기화할까요?`)) return;
  try {
    const res = await Users.resetPassword(item.id);
    tempPw.value = res.data;
    tempPwDialog.value = true;
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '초기화 실패', 'error');
  }
}

async function copyTemp() {
  if (!tempPw.value) return;
  try {
    await navigator.clipboard.writeText(tempPw.value.temp_password);
    notify('클립보드에 복사됨');
  } catch {
    notify('복사 실패', 'error');
  }
}

onMounted(load);
</script>
