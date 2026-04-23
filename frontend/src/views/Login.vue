<template>
  <v-app>
    <v-main class="login-bg">
      <div class="d-flex justify-center align-center" style="min-height: 100vh">
        <v-card width="500" class="pa-2 login-card" elevation="8">
          <v-card-title class="d-flex align-center pa-4">
            <v-icon icon="mdi-hub" size="36" color="primary" class="mr-3" />
            <div>
              <div class="text-h5">NOA Interface Hub</div>
              <div class="text-caption text-medium-emphasis">보험사 인터페이스 통합 관제</div>
            </div>
          </v-card-title>
          <v-divider />

          <v-card-text class="pa-4">
            <v-alert
              v-if="reason === 'expired'"
              type="warning"
              variant="tonal"
              density="compact"
              class="mb-3"
            >
              세션이 만료되어 다시 로그인해주세요.
            </v-alert>

            <v-text-field
              v-model="username"
              label="사용자명"
              :placeholder="hintUsername"
              persistent-placeholder
              prepend-inner-icon="mdi-account-outline"
              variant="outlined"
              density="comfortable"
              autocomplete="username"
              autofocus
              @keyup.enter="onSubmit"
            />
            <v-text-field
              v-model="password"
              label="비밀번호"
              :placeholder="hintPassword"
              persistent-placeholder
              prepend-inner-icon="mdi-lock-outline"
              :type="showPw ? 'text' : 'password'"
              :append-inner-icon="showPw ? 'mdi-eye-off' : 'mdi-eye'"
              variant="outlined"
              density="comfortable"
              autocomplete="current-password"
              @click:append-inner="showPw = !showPw"
              @keyup.enter="onSubmit"
            />

            <v-alert v-if="errMsg" type="error" variant="tonal" density="compact" class="mt-2">
              {{ errMsg }}
            </v-alert>

            <v-btn
              color="primary"
              variant="elevated"
              block
              size="large"
              class="mt-3"
              :loading="submitting"
              :disabled="!username || !password"
              @click="onSubmit"
            >
              로그인
            </v-btn>

            <v-divider class="my-4" />

            <div class="text-caption text-medium-emphasis mb-2">
              <v-icon icon="mdi-information-outline" size="x-small" />
              평가용 데모 계정 — 카드 클릭 시 자동 입력 후 로그인
            </div>

            <div class="demo-grid">
              <div
                v-for="d in demoAccounts"
                :key="d.username"
                class="demo-card"
                :class="`demo-${d.color}`"
                @click="fillAndLogin(d)"
              >
                <div class="d-flex align-center mb-1">
                  <v-icon :icon="d.icon" size="small" :color="d.color" class="mr-2" />
                  <strong>{{ d.title }}</strong>
                  <v-chip
                    size="x-small"
                    variant="flat"
                    :color="d.color"
                    class="ml-auto"
                  >
                    {{ d.username }}
                  </v-chip>
                </div>
                <div class="text-caption text-medium-emphasis">
                  {{ d.description }}
                </div>
                <div class="text-caption mt-1" style="font-size: 11px">
                  <v-icon icon="mdi-check-circle" size="x-small" color="success" />
                  {{ d.canDo }}
                </div>
                <div v-if="d.cannotDo" class="text-caption" style="font-size: 11px">
                  <v-icon icon="mdi-close-circle" size="x-small" color="error" />
                  {{ d.cannotDo }}
                </div>
              </div>
            </div>
          </v-card-text>
        </v-card>
      </div>
    </v-main>
  </v-app>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const username = ref('');
const password = ref('');
const showPw = ref(false);
const submitting = ref(false);
const errMsg = ref('');

const reason = computed(() => route.query.reason as string | undefined);

// 입력란 placeholder — 평가관에게 아래 데모 카드 클릭 안내
const hintUsername = '아래 데모 계정 카드를 클릭하세요 ↓';
const hintPassword = '카드 클릭 시 자동 입력됩니다';

const demoAccounts = [
  {
    title: '관리자',
    username: 'admin',
    password: 'admin1234',
    color: 'error',
    icon: 'mdi-shield-crown-outline',
    description: '시스템 관리자 — 모든 기능 접근',
    canDo: '인터페이스 등록·수정·보관/복원·시크릿 관리·실행·재처리·장애 처리·SLA 목표 변경',
    cannotDo: '',
  },
  {
    title: '운영자',
    username: 'operator',
    password: 'op1234',
    color: 'warning',
    icon: 'mdi-account-hard-hat-outline',
    description: '현장 운영자 — 실행과 장애 대응 담당',
    canDo: '▶ 수동 실행 · ↻ 재처리 (단건/일괄) · 장애 해결 처리',
    cannotDo: '인터페이스 등록·수정·보관, 시크릿 변경, SLA 목표 변경 불가',
  },
  {
    title: '감사자',
    username: 'viewer',
    password: 'view1234',
    color: 'info',
    icon: 'mdi-eye-outline',
    description: '감사관·임원·신입 — 읽기 전용',
    canDo: '모든 화면 조회 + Excel 다운로드 + 프린트(PDF) 가능',
    cannotDo: '실행·재처리·장애 처리·등록 등 모든 변경 액션 불가',
  },
];

function fill(d: { username: string; password: string }) {
  username.value = d.username;
  password.value = d.password;
}

async function fillAndLogin(d: { username: string; password: string }) {
  fill(d);
  await onSubmit();
}

async function onSubmit() {
  if (!username.value || !password.value) return;
  submitting.value = true;
  errMsg.value = '';
  try {
    await auth.login(username.value.trim(), password.value);
    const target = (route.query.next as string) || '/dashboard';
    router.push(target);
  } catch (e: any) {
    errMsg.value = e?.response?.data?.detail ?? '로그인 실패';
  } finally {
    submitting.value = false;
  }
}
</script>

<style scoped>
.login-bg {
  background: linear-gradient(135deg, #1f3a93 0%, #4b6587 100%);
}
.login-card {
  border-radius: 12px;
}

.demo-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.demo-card {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  background: #fff;
}
.demo-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
}
.demo-card.demo-error:hover { border-color: #e04f5f; }
.demo-card.demo-warning:hover { border-color: #f6a623; }
.demo-card.demo-info:hover { border-color: #2196f3; }
</style>