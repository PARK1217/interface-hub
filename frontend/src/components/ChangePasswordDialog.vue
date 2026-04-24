<!--
  비밀번호 변경 다이얼로그 (Phase B.3 / B.4 공용).

  - props.forced=true 면 닫기 버튼 / 외부 클릭 닫기 비활성 → 강제 변경 모드
    (Phase B.4: 관리자 발급 임시 비밀번호로 처음 로그인한 사용자 차단)
  - 정책: 8자 이상 + 영문/숫자/특수문자 모두 포함 + 사용자명·직전과 다름
    (서버에서 한 번 더 검증되지만, UX 위해 클라이언트 즉시 피드백)
-->
<template>
  <v-dialog
    :model-value="modelValue"
    :persistent="forced"
    max-width="480"
    @update:model-value="(v) => !forced && emit('update:modelValue', v)"
  >
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon
          :icon="forced ? 'mdi-shield-key-outline' : 'mdi-lock-reset'"
          :color="forced ? 'warning' : 'primary'"
          class="mr-2"
        />
        {{ forced ? '비밀번호 변경 필요' : '비밀번호 변경' }}
      </v-card-title>

      <v-card-text>
        <v-alert
          v-if="forced"
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-3"
        >
          <strong>임시 비밀번호로 로그인했습니다.</strong> 보안 정책에 따라
          새 비밀번호로 변경해야 다른 화면을 사용할 수 있습니다.
        </v-alert>

        <v-text-field
          v-model="currentPw"
          label="현재 비밀번호"
          type="password"
          variant="outlined"
          density="comfortable"
          autocomplete="current-password"
          autofocus
        />
        <v-text-field
          v-model="newPw"
          label="새 비밀번호"
          type="password"
          variant="outlined"
          density="comfortable"
          autocomplete="new-password"
          :hint="policyHint"
          persistent-hint
          :error-messages="newPwError ? [newPwError] : []"
        />
        <v-text-field
          v-model="confirmPw"
          label="새 비밀번호 확인"
          type="password"
          variant="outlined"
          density="comfortable"
          class="mt-2"
          autocomplete="new-password"
          :error-messages="confirmError ? [confirmError] : []"
          @keyup.enter="onSubmit"
        />

        <!-- 클라이언트 정책 체크리스트 (서버 검증과 동일 기준) -->
        <div class="text-caption mt-2">
          <div :class="rules.length ? 'text-success' : 'text-medium-emphasis'">
            <v-icon size="x-small" :icon="rules.length ? 'mdi-check-circle' : 'mdi-circle-outline'" />
            최소 8자
          </div>
          <div :class="rules.complexity ? 'text-success' : 'text-medium-emphasis'">
            <v-icon size="x-small" :icon="rules.complexity ? 'mdi-check-circle' : 'mdi-circle-outline'" />
            영문 · 숫자 · 특수문자 모두 포함
          </div>
          <div :class="rules.notSameAsUsername ? 'text-success' : 'text-medium-emphasis'">
            <v-icon size="x-small" :icon="rules.notSameAsUsername ? 'mdi-check-circle' : 'mdi-circle-outline'" />
            사용자명과 다름
          </div>
          <div :class="rules.notSameAsCurrent ? 'text-success' : 'text-medium-emphasis'">
            <v-icon size="x-small" :icon="rules.notSameAsCurrent ? 'mdi-check-circle' : 'mdi-circle-outline'" />
            현재 비밀번호와 다름
          </div>
        </div>

        <v-alert v-if="serverError" type="error" variant="tonal" density="compact" class="mt-3">
          {{ serverError }}
        </v-alert>
      </v-card-text>

      <v-card-actions>
        <v-btn v-if="forced" variant="text" color="grey" @click="onLogout">로그아웃</v-btn>
        <v-spacer />
        <v-btn v-if="!forced" variant="text" @click="emit('update:modelValue', false)">취소</v-btn>
        <v-btn
          color="primary"
          variant="elevated"
          :loading="busy"
          :disabled="!canSubmit"
          @click="onSubmit"
        >
          변경
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Auth } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

const props = defineProps<{
  modelValue: boolean;
  forced?: boolean;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'changed'): void;
}>();

const auth = useAuthStore();
const router = useRouter();

const currentPw = ref('');
const newPw = ref('');
const confirmPw = ref('');
const busy = ref(false);
const serverError = ref('');

const policyHint = '8자 이상 · 영문 · 숫자 · 특수문자 모두 포함';

const rules = computed(() => ({
  length: newPw.value.length >= 8,
  complexity:
    /[A-Za-z]/.test(newPw.value) &&
    /\d/.test(newPw.value) &&
    /[!@#$%^&*()\-_=+\[\]{};:'",.<>/?\\|`~]/.test(newPw.value),
  notSameAsUsername:
    !!newPw.value &&
    !!auth.user?.username &&
    newPw.value.toLowerCase() !== auth.user.username.toLowerCase(),
  notSameAsCurrent: !!newPw.value && newPw.value !== currentPw.value,
}));

const newPwError = computed(() => {
  if (!newPw.value) return '';
  if (!rules.value.length) return '8자 이상 입력하세요.';
  if (!rules.value.complexity) return '영문·숫자·특수문자를 모두 포함해야 합니다.';
  if (!rules.value.notSameAsUsername) return '사용자명과 같을 수 없습니다.';
  if (!rules.value.notSameAsCurrent) return '현재 비밀번호와 같을 수 없습니다.';
  return '';
});

const confirmError = computed(() => {
  if (!confirmPw.value) return '';
  return confirmPw.value === newPw.value ? '' : '두 입력이 일치하지 않습니다.';
});

const canSubmit = computed(
  () =>
    !!currentPw.value &&
    !!newPw.value &&
    confirmPw.value === newPw.value &&
    rules.value.length &&
    rules.value.complexity &&
    rules.value.notSameAsUsername &&
    rules.value.notSameAsCurrent,
);

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      currentPw.value = '';
      newPw.value = '';
      confirmPw.value = '';
      serverError.value = '';
    }
  },
);

async function onSubmit() {
  if (!canSubmit.value) return;
  busy.value = true;
  serverError.value = '';
  try {
    // Phase B.6 — 응답에 새 access_token 포함. 기존 토큰은 서버에서 무효화되므로
    // 즉시 교체. setToken 을 setUser 보다 먼저 호출해야 다음 axios 호출이 새 토큰 사용.
    const res = await Auth.changePassword(currentPw.value, newPw.value);
    auth.setToken(res.data.access_token, res.data.expires_at);
    auth.setUser(res.data.user);
    emit('changed');
    emit('update:modelValue', false);
  } catch (e: any) {
    serverError.value = e?.response?.data?.detail ?? '변경 실패';
  } finally {
    busy.value = false;
  }
}

async function onLogout() {
  // 강제 변경 모드에서 사용자가 거부하면 로그아웃 외엔 출구 없음
  await auth.logout();
  router.push('/login');
}
</script>