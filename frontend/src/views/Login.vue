<template>
  <v-app>
    <v-main class="login-bg">
      <div class="d-flex justify-center align-center" style="min-height: 100vh">
        <v-card width="500" class="pa-2 login-card" elevation="8">
          <v-card-title class="d-flex align-center pa-4">
            <v-icon icon="mdi-hub" size="36" color="primary" class="mr-3" />
            <div>
              <div class="text-h5">Interface Hub</div>
              <div class="text-caption text-medium-emphasis">보험사 인터페이스 통합 관제</div>
            </div>
          </v-card-title>
          <v-divider />

          <v-card-text class="pa-4">
            <!-- 문서 링크 — 평가관이 로그인 전에 먼저 확인할 수 있게 최상단 배치 -->
            <div class="doc-links mb-4">
              <div class="text-subtitle-2 mb-2 d-flex align-center">
                <v-icon icon="mdi-book-open-page-variant-outline" size="small" color="primary" class="mr-1" />
                평가관 안내 — 먼저 문서를 확인해주세요
              </div>
              <div class="d-flex" style="gap: 10px">
                <v-btn
                  color="primary"
                  variant="flat"
                  size="large"
                  prepend-icon="mdi-file-pdf-box"
                  :href="proposalHref"
                  target="_blank"
                  rel="noopener"
                  style="flex: 1"
                >
                  기획서 (PDF)
                </v-btn>
                <v-btn
                  color="info"
                  variant="flat"
                  size="large"
                  prepend-icon="mdi-file-document-outline"
                  :href="developmentHref"
                  target="_blank"
                  rel="noopener"
                  style="flex: 1"
                >
                  개발 문서 (HTML)
                </v-btn>
              </div>
            </div>

            <v-divider class="mb-4" />

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

// 문서 링크 — 백엔드 정적 서빙 (/api/docs-file/...). 파일명 공백·특수문자는 encodeURI 로 처리.
// file/ 폴더 안에서 실제 파일명이 바뀌면 아래 상수만 교체.
// ?v=__BUILD_TS__ : 빌드 시점 타임스탬프를 쿼리로 붙여 PDF/HTML 캐시를 무효화.
//   Chrome PDF 뷰어가 같은 URL 의 PDF 를 매우 공격적으로 캐시해서 새 기획서가
//   안 보이는 문제가 있었음. 배포마다 URL 이 바뀌면 무조건 새로 받음.
const proposalHref = `/api/docs-file/${encodeURIComponent('Interface_Hub_기획서.pdf')}?v=${__BUILD_TS__}`;
const developmentHref = `/api/docs-file/${encodeURIComponent('Interface Hub - _.html')}?v=${__BUILD_TS__}`;

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
    // 423 LOCKED 는 401 과 별도 — 백엔드 detail 메시지에 잠금 안내 포함
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
/* 문서 링크 박스 — 평가관 첫 시선에 강조 */
.doc-links {
  padding: 12px 14px;
  background: rgba(25, 118, 210, 0.06);
  border: 1px solid rgba(25, 118, 210, 0.2);
  border-radius: 8px;
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