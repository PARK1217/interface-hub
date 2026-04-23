<template>
  <v-app>
    <v-main class="login-bg">
      <div class="d-flex justify-center align-center" style="min-height: 100vh">
        <v-card width="420" class="pa-2 login-card" elevation="8">
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
              <v-icon icon="mdi-information-outline" size="x-small" /> 평가용 데모 계정 (클릭 시 자동 입력)
            </div>
            <div class="d-flex flex-wrap" style="gap:6px">
              <v-chip
                v-for="d in demoAccounts"
                :key="d.username"
                size="small"
                variant="tonal"
                :color="d.color"
                @click="fill(d)"
              >
                {{ d.label }}
              </v-chip>
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

// 입력란 placeholder — 흐릿하게 데모 계정 노출 (평가관 진입 편의)
const hintUsername = 'admin / operator / viewer';
const hintPassword = 'admin1234 / op1234 / view1234';

const demoAccounts = [
  { label: '관리자 (admin)', username: 'admin', password: 'admin1234', color: 'error' },
  { label: '운영자 (operator)', username: 'operator', password: 'op1234', color: 'warning' },
  { label: '감사자 (viewer)', username: 'viewer', password: 'view1234', color: 'info' },
];

function fill(d: { username: string; password: string }) {
  username.value = d.username;
  password.value = d.password;
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
</style>