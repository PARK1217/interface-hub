<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">AI 분석 어시스턴트</h2>
      <v-spacer />
      <v-chip
        v-if="aiStatus"
        :color="aiStatus.configured ? 'success' : 'info'"
        variant="tonal"
        size="small"
        :prepend-icon="aiStatus.configured ? 'mdi-robot-happy' : 'mdi-text-search'"
        class="mr-2"
      >
        {{ aiStatus.configured ? `LLM · ${aiStatus.provider}` : 'Fallback (TF-IDF)' }}
        <span v-if="aiStatus.model" class="ml-1 text-caption">({{ aiStatus.model }})</span>
      </v-chip>
      <v-btn
        variant="text"
        size="small"
        :prepend-icon="showHistory ? 'mdi-history' : 'mdi-history'"
        @click="toggleHistory"
      >
        내 히스토리
      </v-btn>
    </div>

    <v-alert v-if="aiStatus && !aiStatus.configured" type="info" variant="tonal" density="compact" class="mb-4">
      LLM 키 미설정 → 키워드 매칭(TF-IDF)으로 과거 장애 사례 검색·템플릿 응답.
      <code>backend/.env</code> 의 <code>AI_PROVIDER</code> + 해당 키 (Mistral/Anthropic/
      HuggingFace/OpenAI) 를 채우면 자동으로 LLM 모드로 전환됩니다.
    </v-alert>

    <!-- 검색·분석 단계 사유 (no_history / no_match / empty_question / scikit_missing) -->
    <v-alert
      v-if="analysisNote"
      :type="analysisNote.kind === 'no_match' ? 'info' : 'warning'"
      variant="tonal"
      density="compact"
      class="mb-3"
    >
      <div class="d-flex align-start">
        <v-icon :icon="analysisIcon(analysisNote.kind)" class="mr-2 mt-1" />
        <div style="flex: 1">
          <div class="text-subtitle-2">{{ analysisNote.title }}</div>
          <div class="text-body-2 mt-1">{{ analysisNote.detail }}</div>
          <div class="text-caption mt-1" style="opacity: 0.85">
            <v-icon icon="mdi-lightbulb-outline" size="x-small" class="mr-1" />
            <strong>권장:</strong> {{ analysisNote.suggestion }}
          </div>
        </div>
      </div>
    </v-alert>

    <!-- LLM 호출 단계 실패 사유 — title / detail / suggestion 3단 -->
    <v-alert
      v-if="llmError"
      :type="llmErrorAlertType(llmError.kind)"
      variant="tonal"
      density="compact"
      class="mb-4"
    >
      <div class="d-flex align-start">
        <v-icon :icon="llmErrorIcon(llmError.kind)" class="mr-2 mt-1" />
        <div style="flex: 1">
          <div class="text-subtitle-2 d-flex align-center">
            <span>{{ llmError.title }}</span>
            <v-chip
              v-if="llmError.status"
              size="x-small"
              variant="flat"
              :color="llmError.status >= 500 ? 'error' : 'warning'"
              class="ml-2"
            >
              HTTP {{ llmError.status }}
            </v-chip>
            <v-chip
              size="x-small"
              variant="flat"
              color="grey-darken-1"
              class="ml-2"
            >
              {{ llmError.provider }} · {{ kindLabel(llmError.kind) }}
            </v-chip>
          </div>
          <div class="text-body-2 mt-1">{{ llmError.detail }}</div>
          <div class="text-caption mt-1" style="opacity: 0.85">
            <v-icon icon="mdi-lightbulb-outline" size="x-small" class="mr-1" />
            <strong>권장 조치:</strong> {{ llmError.suggestion }}
          </div>
          <div v-if="llmError.body_excerpt" class="mt-2">
            <v-expansion-panels flat variant="accordion">
              <v-expansion-panel>
                <v-expansion-panel-title class="text-caption pa-2">
                  프로바이더 응답 본문 보기
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <pre class="text-caption" style="white-space: pre-wrap; margin: 0">{{ llmError.body_excerpt }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>
        </div>
      </div>
    </v-alert>

    <v-row>
      <v-col :cols="showHistory ? 8 : 12">
        <v-card class="mb-4">
          <v-card-text>
            <v-textarea
              v-model="question"
              label="장애 상황을 자연어로 설명하세요"
              rows="3"
              placeholder="예) 보험개발원 연동 API 가 오늘 오후 3시 이후 응답 지연 중이야. 원인 분석해줘."
            />

            <!-- 동적 prompt 추천. popular > incident > interface 템플릿 자동 분기 -->
            <div v-if="suggestions.length" class="mb-2">
              <div class="text-caption text-medium-emphasis mb-1 d-flex align-center">
                <v-icon icon="mdi-lightbulb-outline" size="small" class="mr-1" />
                추천 프롬프트
                <v-chip
                  v-if="suggestions.some(s => s.source === 'popular')"
                  size="x-small"
                  variant="tonal"
                  color="success"
                  class="ml-2"
                >
                  자주 묻는 질문 포함
                </v-chip>
                <v-chip
                  v-else
                  size="x-small"
                  variant="tonal"
                  color="grey"
                  class="ml-2"
                  title="아직 질의 이력이 부족해 등록된 인터페이스 + 최근 장애 기반으로 자동 생성"
                >
                  데이터 기반 자동 생성
                </v-chip>
              </div>
              <div class="d-flex flex-wrap" style="gap: 6px">
                <v-chip
                  v-for="(s, i) in suggestions"
                  :key="i"
                  size="small"
                  variant="tonal"
                  :color="suggestionColor(s.source)"
                  :prepend-icon="suggestionIcon(s.source)"
                  @click="question = s.text"
                  :title="s.text"
                  style="max-width: 100%; height: auto"
                >
                  {{ s.text.length > 40 ? s.text.slice(0, 40) + '…' : s.text }}
                </v-chip>
              </div>
            </div>

            <div class="d-flex align-center mt-2">
              <v-spacer />
              <v-btn color="primary" @click="ask" :loading="loading" :disabled="!question.trim()">
                <v-icon start icon="mdi-robot" /> 분석 요청
              </v-btn>
            </div>
          </v-card-text>
        </v-card>

        <!-- 분석 진행 중 — 단계별 메시지를 회전 표시 (실제 백엔드 단계와 1:1 동기화는 아님, UX 안심용) -->
        <v-card v-if="loading" class="mb-4">
          <v-card-text class="text-center py-6">
            <v-progress-circular indeterminate color="primary" size="36" width="3" class="mb-3" />
            <div class="text-body-1 mb-1">{{ progressMessage }}</div>
            <div class="text-caption text-medium-emphasis">{{ elapsedSec }}초 경과</div>
            <v-progress-linear indeterminate color="primary" class="mt-4" rounded height="3" />
          </v-card-text>
        </v-card>

        <v-card v-if="answer && !loading" class="mb-4">
          <v-card-title class="d-flex align-center" style="flex-wrap: wrap; gap: 6px">
            <span>분석 결과</span>
            <v-chip
              v-if="lastIntent && lastIntent !== 'general'"
              size="x-small"
              :color="intentColor(lastIntent)"
              variant="flat"
              :prepend-icon="intentIcon(lastIntent)"
              :title="intentTooltip(lastIntent)"
            >
              {{ intentLabel(lastIntent) }}
            </v-chip>
            <v-chip
              v-if="lastMode"
              size="x-small"
              :color="lastMode === 'llm' ? 'success' : 'info'"
            >
              {{ lastMode }}{{ lastProvider ? ` · ${lastProvider}` : '' }}
            </v-chip>
            <v-chip
              v-if="lastCached"
              size="x-small"
              color="amber-darken-2"
              variant="flat"
              prepend-icon="mdi-flash"
              title="Redis 캐시 hit — LLM 호출 없이 즉시 응답"
            >
              cached
            </v-chip>
            <v-chip
              v-if="lastRepeated"
              size="x-small"
              color="grey-darken-2"
              variant="flat"
              prepend-icon="mdi-history"
              title="이 질문은 24시간 안에 이미 분석 불가로 판정됨 — LLM/DB 호출 생략"
            >
              repeated
            </v-chip>
            <!-- fallback 체인이 쓰인 경우 표시 -->
            <v-chip
              v-if="llmAttempts.length && lastMode === 'llm'"
              size="x-small"
              color="orange-darken-2"
              variant="flat"
              prepend-icon="mdi-swap-horizontal"
              :title="fallbackChainTooltip"
            >
              fallback · {{ llmAttempts.length }}건 복구
            </v-chip>
          </v-card-title>

          <!-- 체인 상세 — 어느 프로바이더가 왜 실패했는지 투명하게 노출 -->
          <v-card-subtitle v-if="llmAttempts.length && lastMode === 'llm'" class="pt-0">
            <v-icon icon="mdi-information-outline" size="x-small" class="mr-1" />
            <span v-for="(a, i) in llmAttempts" :key="i" class="text-caption">
              <strong>{{ a.provider }}</strong> ({{ a.status ? `HTTP ${a.status}` : kindLabel(a.kind) }})
              <v-icon icon="mdi-arrow-right" size="x-small" class="mx-1" />
            </span>
            <strong class="text-success">{{ lastProvider }}</strong> 성공
          </v-card-subtitle>
          <v-card-text class="ai-answer-md" v-html="renderedAnswer" @click="handleAnswerClick" />
        </v-card>

        <v-card v-if="cases.length">
          <v-card-title class="d-flex align-center">
            유사 사례 Top-{{ cases.length }}
            <v-chip
              size="x-small"
              color="info"
              variant="tonal"
              class="ml-2"
              prepend-icon="mdi-cursor-default-click-outline"
            >
              클릭 시 장애 페이지로 드릴다운
            </v-chip>
          </v-card-title>
          <v-list>
            <v-list-item
              v-for="c in cases"
              :key="c.incident_id"
              link
              :title="`incident #${c.incident_id} 상세 보기 (장애 페이지로 이동)`"
              @click="drillIntoIncident(c.incident_id)"
            >
              <v-list-item-title>
                #{{ c.incident_id }} · {{ c.type }} ·
                <v-chip size="x-small" variant="tonal" :color="scoreColor(c.score)">
                  유사도 {{ (c.score * 100).toFixed(1) }}%
                </v-chip>
                <v-icon icon="mdi-open-in-new" size="x-small" class="ml-1" />
              </v-list-item-title>
              <v-list-item-subtitle style="white-space: pre-wrap">{{ c.content }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- 본인 대화 히스토리 사이드 패널 -->
      <v-col v-if="showHistory" cols="4">
        <v-card variant="flat" border>
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-history" class="mr-2" />
            <span class="text-subtitle-1">내 질의 이력</span>
            <v-spacer />
            <v-switch
              v-model="historyIncludeFailed"
              label="실패 포함"
              hide-details
              density="compact"
              color="warning"
              class="mr-2 history-filter-switch"
              @update:model-value="loadHistory"
            />
            <v-btn icon="mdi-refresh" size="x-small" variant="text" @click="loadHistory" />
          </v-card-title>
          <v-card-text v-if="!history.length" class="text-center text-medium-emphasis">
            아직 이력이 없습니다. 질문을 하면 여기에 쌓입니다.
          </v-card-text>
          <v-list v-else density="compact" max-height="600" style="overflow-y: auto">
            <v-list-item
              v-for="h in history"
              :key="h.id"
              :title="h.question.length > 60 ? h.question.slice(0, 60) + '…' : h.question"
              @click="replayHistory(h)"
            >
              <template #title>
                <div class="text-body-2" style="white-space: normal">
                  {{ h.question.length > 60 ? h.question.slice(0, 60) + '…' : h.question }}
                </div>
              </template>
              <template #subtitle>
                <div class="d-flex align-center" style="gap: 4px; flex-wrap: wrap">
                  <v-chip
                    v-if="h.outcome && h.outcome !== 'success'"
                    size="x-small"
                    color="grey-darken-1"
                    variant="flat"
                    prepend-icon="mdi-close-circle-outline"
                    :title="`분석 불가 사유: ${h.outcome}`"
                  >
                    {{ outcomeLabel(h.outcome) }}
                  </v-chip>
                  <v-chip
                    v-else
                    size="x-small"
                    :color="h.mode === 'llm' ? 'success' : 'info'"
                    variant="tonal"
                  >
                    {{ h.mode }}{{ h.provider ? ` · ${h.provider}` : '' }}
                  </v-chip>
                  <v-chip
                    v-if="h.cached"
                    size="x-small"
                    color="amber-darken-2"
                    variant="flat"
                    prepend-icon="mdi-flash"
                  >
                    cached
                  </v-chip>
                  <v-chip
                    v-if="h.llm_error_kind"
                    size="x-small"
                    color="warning"
                    variant="tonal"
                  >
                    {{ kindLabel(h.llm_error_kind) }}
                  </v-chip>
                  <span class="text-caption text-medium-emphasis ml-1">{{ formatDateTimeShort(h.asked_at) }}</span>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>
    </v-row>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="6000">
      {{ snack.text }}
    </v-snackbar>
  </div>
</template>

<script setup lang="ts">
// keep-alive include 매칭용 — 페이지 이동 후 돌아와도 답변/입력 상태 유지
defineOptions({ name: 'AiAssistant' });

import { computed, onMounted, reactive, ref } from 'vue';
import { AI, type AiHistoryItem, type AiSuggestion } from '@/api/client';
import { formatDateTimeShort } from '@/utils/format';
import { useRouter } from 'vue-router';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const router = useRouter();

function drillIntoIncident(incidentId: number) {
  // case_lookup 결과의 유사 사례를 클릭하면 incidents 페이지에서
  // 해당 행을 자동 강조. ?focus=ID 쿼리는 Incidents.vue 가 onMounted 에서 읽음.
  router.push({ path: '/incidents', query: { focus: String(incidentId) } });
}

interface LLMErrorPayload {
  kind: string;
  provider: string;
  title: string;
  detail: string;
  suggestion: string;
  message: string;
  status?: number | null;
  body_excerpt?: string | null;
}

interface AnalysisNotePayload {
  kind: 'no_history' | 'no_match' | 'empty_question' | 'scikit_missing' | string;
  title: string;
  detail: string;
  suggestion: string;
}

interface LLMAttempt {
  provider: string;
  kind: string;
  status: number | null;
  title: string;
}

const question = ref('');
const answer = ref('');
// LLM 응답을 markdown 으로 렌더 (표/볼드/헤더/리스트 등). 항상 sanitize 필수.
marked.setOptions({ gfm: true, breaks: true });

const renderedAnswer = computed(() => {
  if (!answer.value) return '';
  const raw = marked.parse(answer.value, { async: false }) as string;
  const safe = DOMPurify.sanitize(raw);
  // 안전망: LLM 이 markdown 링크 지시를 안 따랐을 때 plain "#42" 패턴을 anchor 로.
  // 이미 a 태그 안에 들어간 #N 은 건드리지 않음.
  return safe.replace(/(<a [^>]*>.*?<\/a>)|(#\d+\b)/gi, (match, anchor, plain) => {
    if (anchor) return anchor;
    if (plain) {
      const id = plain.slice(1);
      return `<a href="/incidents?focus=${id}" class="ai-incident-link">${plain}</a>`;
    }
    return match;
  });
});

// v-html 로 렌더된 a 태그는 router-link 가 아니라 일반 anchor — SPA 라우팅 유지를 위해
// 클릭을 가로채서 router.push 로 이동. 외부 URL (http://) 은 그대로 둠.
function handleAnswerClick(e: MouseEvent) {
  const t = e.target as HTMLElement | null;
  const a = t?.closest('a') as HTMLAnchorElement | null;
  if (!a) return;
  const href = a.getAttribute('href') || '';
  if (href.startsWith('/')) {
    e.preventDefault();
    router.push(href);
  }
}
const cases = ref<{ incident_id: number; type: string; content: string; score: number }[]>([]);
const lastMode = ref<'llm' | 'fallback' | null>(null);
const lastProvider = ref<string | null>(null);
const lastCached = ref(false);
const lastIntent = ref<string | null>(null);
const lastRepeated = ref(false);
const historyIncludeFailed = ref(false);
const llmError = ref<LLMErrorPayload | null>(null);
const llmAttempts = ref<LLMAttempt[]>([]);
const analysisNote = ref<AnalysisNotePayload | null>(null);
const loading = ref(false);
const snack = reactive({ show: false, text: '', color: 'error' });

const aiStatus = ref<{ configured: boolean; provider: string; model: string | null } | null>(null);
const suggestions = ref<AiSuggestion[]>([]);
const history = ref<AiHistoryItem[]>([]);
const showHistory = ref(true);  // 기본 펼침 — 사용자가 메뉴 존재 자체를 모를 수 있음

onMounted(async () => {
  try {
    aiStatus.value = (await AI.status()).data;
  } catch {
    /* ignore */
  }
  await loadSuggestions();
  // 히스토리 패널이 기본 펼침이라 데이터도 미리 로드
  if (showHistory.value) await loadHistory();
});

async function loadSuggestions() {
  try {
    suggestions.value = (await AI.suggestions(4)).data;
  } catch {
    suggestions.value = [];
  }
}

async function loadHistory() {
  try {
    history.value = (await AI.myHistory(20, historyIncludeFailed.value)).data;
  } catch {
    history.value = [];
  }
}

const fallbackChainTooltip = computed(() => {
  if (!llmAttempts.value.length) return '';
  const steps = llmAttempts.value.map(a =>
    `${a.provider} ${a.status ? 'HTTP ' + a.status : a.kind}`
  );
  return `체인: ${steps.join(' → ')} → ${lastProvider.value} 성공`;
});

function outcomeLabel(outcome: string): string {
  switch (outcome) {
    case 'empty_question': return '너무 짧음';
    case 'no_history': return '과거 사례 없음';
    case 'no_match': return '유사 사례 없음';
    case 'scikit_missing': return '엔진 미설치';
    case 'llm_failed': return 'LLM 실패';
    default: return outcome;
  }
}

async function toggleHistory() {
  showHistory.value = !showHistory.value;
  if (showHistory.value && history.value.length === 0) await loadHistory();
}

function scoreColor(s: number) {
  if (s > 0.5) return 'success';
  if (s > 0.2) return 'warning';
  return 'grey';
}

function suggestionColor(src: string): string {
  return src === 'popular' ? 'success' : src === 'incident_recent' ? 'error' : 'primary';
}

function suggestionIcon(src: string): string {
  return src === 'popular'
    ? 'mdi-fire'
    : src === 'incident_recent'
    ? 'mdi-alert-circle-outline'
    : 'mdi-api';
}

function kindLabel(kind: string): string {
  switch (kind) {
    // 설정 오류
    case 'not_configured': return '설정 누락';
    case 'auth_failed': return 'API 키 인증 실패';
    case 'forbidden': return '모델 권한 부족';
    case 'model_not_found': return '모델 미발견';
    // 프로바이더 일시 장애
    case 'rate_limited': return '요청 한도 초과';
    case 'server_error': return '프로바이더 서버 오류';
    case 'gateway_unreachable': return '프로바이더 게이트웨이 장애';
    // 네트워크/응답
    case 'timeout': return '응답 시간 초과';
    case 'network': return '네트워크 오류';
    case 'empty_response': return '빈 응답';
    case 'parse_error': return '응답 형식 오류';
    // 기타
    case 'unknown': return '알 수 없는 오류';
    case 'http_error': return 'HTTP 오류';
    default: return kind;
  }
}

// kind 별 alert 색상 — 설정 오류는 운영자가 즉시 해결 가능 (warning),
// 프로바이더 일시 장애는 시간 지나면 회복 (info), 네트워크/파싱은 환경 문제 (error).
function llmErrorAlertType(kind: string): 'error' | 'warning' | 'info' {
  if (['not_configured'].includes(kind)) return 'info';
  if (['auth_failed', 'forbidden', 'model_not_found'].includes(kind)) return 'warning';
  if (['rate_limited', 'gateway_unreachable', 'server_error'].includes(kind)) return 'info';
  if (['timeout', 'network', 'parse_error', 'empty_response'].includes(kind)) return 'error';
  return 'warning';
}

function llmErrorIcon(kind: string): string {
  if (['auth_failed', 'forbidden'].includes(kind)) return 'mdi-key-alert-outline';
  if (kind === 'model_not_found') return 'mdi-help-rhombus-outline';
  if (kind === 'rate_limited') return 'mdi-speedometer-slow';
  if (['server_error', 'gateway_unreachable'].includes(kind)) return 'mdi-server-network-off';
  if (kind === 'timeout') return 'mdi-clock-alert-outline';
  if (kind === 'network') return 'mdi-lan-disconnect';
  if (kind === 'empty_response') return 'mdi-comment-question-outline';
  if (kind === 'parse_error') return 'mdi-code-tags';
  if (kind === 'not_configured') return 'mdi-cog-off-outline';
  return 'mdi-robot-confused-outline';
}

function intentLabel(intent: string): string {
  switch (intent) {
    case 'stats_query': return '운영 통계 분석';
    case 'case_lookup': return '과거 사례 검색';
    case 'config_query': return '설정 정보 조회';
    default: return '일반 질의';
  }
}
function intentIcon(intent: string): string {
  switch (intent) {
    case 'stats_query': return 'mdi-chart-bar';
    case 'case_lookup': return 'mdi-book-search-outline';
    case 'config_query': return 'mdi-cog-outline';
    default: return 'mdi-robot-outline';
  }
}
function intentColor(intent: string): string {
  switch (intent) {
    case 'stats_query': return 'deep-purple';
    case 'case_lookup': return 'teal';
    case 'config_query': return 'blue-grey';
    default: return 'grey';
  }
}
function intentTooltip(intent: string): string {
  switch (intent) {
    case 'stats_query':
      return '운영 통계 질문으로 분류 — 최근 7일 인터페이스별 호출/실패율 데이터를 LLM 컨텍스트로 사용';
    case 'case_lookup':
      return '과거 사례 검색 질문 — resolved incident 텍스트 매칭';
    case 'config_query':
      return '설정 정보 질문 — 등록된 인터페이스 메타 정보를 LLM 컨텍스트로 사용';
    default:
      return '일반 질의';
  }
}

function analysisIcon(kind: string): string {
  if (kind === 'no_history') return 'mdi-database-off-outline';
  if (kind === 'no_match') return 'mdi-magnify-close';
  if (kind === 'empty_question') return 'mdi-comment-edit-outline';
  if (kind === 'scikit_missing') return 'mdi-package-variant-remove';
  return 'mdi-information-outline';
}

function describeAxiosError(e: any): string {
  if (!e) return 'AI 분석 실패 (알 수 없는 원인)';
  const detail = e?.response?.data?.detail;
  if (detail) return detail;
  const status = e?.response?.status;
  if (status === 502 || status === 503 || status === 504) {
    return `백엔드 게이트웨이 응답 없음 (HTTP ${status}) — backend 컨테이너가 재기동 중이거나 다운됐을 수 있습니다. docker compose ps 확인.`;
  }
  if (status) return `백엔드 오류 HTTP ${status} — 서버 로그(docker compose logs backend) 확인 필요`;
  if (e?.code === 'ECONNABORTED') {
    return '백엔드 응답이 90초 안에 오지 않았습니다. LLM 호출이 60초 timeout 안에 끝나야 정상이며, 그보다 오래 걸리면 backend 가 멈춰있거나 외부망 차단된 상태일 수 있습니다.';
  }
  if (e?.code === 'ERR_NETWORK') {
    return '백엔드에 연결할 수 없습니다 — backend 컨테이너 가동 상태 확인 (docker compose ps)';
  }
  return e?.message || 'AI 분석 실패';
}

// 분석 진행 메시지 — 실제 백엔드 단계와 1:1 동기화는 아니지만 일반적 흐름 따라 회전.
// 마지막 메시지에서 멈추도록 인덱스 cap.
const PROGRESS_STEPS = [
  '질문 의도를 분석하는 중...',
  '관련 인터페이스 / 과거 사례 검색 중...',
  '유사도 계산 중...',
  'AI 모델에 컨텍스트 전달 중...',
  '답변 생성 중...',
  '결과 정리 및 형식 변환 중...',
  '거의 다 됐습니다, 잠시만요...',
];
const progressIdx = ref(0);
const elapsedSec = ref(0);
const progressMessage = computed(() => PROGRESS_STEPS[Math.min(progressIdx.value, PROGRESS_STEPS.length - 1)]);
let progressTimer: ReturnType<typeof setInterval> | null = null;
let elapsedTimer: ReturnType<typeof setInterval> | null = null;

function startProgress() {
  progressIdx.value = 0;
  elapsedSec.value = 0;
  progressTimer = setInterval(() => { progressIdx.value += 1; }, 1800);
  elapsedTimer = setInterval(() => { elapsedSec.value += 1; }, 1000);
}
function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}

// 히스토리 항목 클릭 → 질문 자동 채우기 + ask() 호출.
// 대부분 캐시 hit 로 즉시 답변 복원 (백엔드 TTL 1시간 + repeated_failure 24시간 가드).
// 캐시 만료된 경우엔 새로 분석되지만 사용자 흐름은 동일.
function replayHistory(h: AiHistoryItem) {
  question.value = h.question;
  ask();
}

async function ask() {
  loading.value = true;
  startProgress();
  try {
    const res = await AI.ask(question.value);
    answer.value = res.data.answer;
    cases.value = res.data.similar_cases;
    lastMode.value = (res.data.mode as 'llm' | 'fallback') ?? 'llm';
    lastProvider.value = res.data.provider ?? null;
    lastCached.value = !!res.data.cached;
    lastIntent.value = (res.data.intent as string | null) ?? 'general';
    lastRepeated.value = !!res.data.repeated_failure;
    llmError.value = (res.data.llm_error as LLMErrorPayload | null) ?? null;
    llmAttempts.value = (res.data.llm_attempts as LLMAttempt[] | undefined) ?? [];
    analysisNote.value = (res.data.analysis_note as AnalysisNotePayload | null) ?? null;
    // 새 질문 후 히스토리 / 추천 모두 갱신 (popular 에 영향 가능)
    if (showHistory.value) await loadHistory();
    await loadSuggestions();
  } catch (e: any) {
    Object.assign(snack, {
      show: true,
      text: describeAxiosError(e),
      color: 'error',
    });
  } finally {
    loading.value = false;
    stopProgress();
  }
}
</script>

<style scoped>
/* v-switch 가 flex 컨테이너 안에서 짜부러져 라벨이 세로로 wrap 되는 것 방지 */
.history-filter-switch {
  flex: 0 0 auto;
}
.history-filter-switch :deep(.v-label) {
  white-space: nowrap;
}

/* AI 답변 markdown 렌더 — 표/헤더/볼드/코드 기본 스타일 */
.ai-answer-md :deep(h1),
.ai-answer-md :deep(h2),
.ai-answer-md :deep(h3),
.ai-answer-md :deep(h4) {
  font-weight: 600;
  margin: 14px 0 8px;
  line-height: 1.3;
}
.ai-answer-md :deep(h1) { font-size: 1.4rem; }
.ai-answer-md :deep(h2) { font-size: 1.2rem; }
.ai-answer-md :deep(h3) { font-size: 1.05rem; }
.ai-answer-md :deep(h4) { font-size: 0.95rem; }
.ai-answer-md :deep(p) {
  margin: 6px 0;
  line-height: 1.55;
}
.ai-answer-md :deep(ul),
.ai-answer-md :deep(ol) {
  margin: 6px 0 6px 20px;
  padding-left: 8px;
}
.ai-answer-md :deep(li) {
  margin: 2px 0;
}
.ai-answer-md :deep(strong) {
  font-weight: 600;
}
.ai-answer-md :deep(code) {
  background: rgba(127, 127, 127, 0.15);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}
.ai-answer-md :deep(pre) {
  background: rgba(127, 127, 127, 0.1);
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}
.ai-answer-md :deep(pre code) {
  background: none;
  padding: 0;
}
.ai-answer-md :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 0.9rem;
  width: auto;
}
.ai-answer-md :deep(th),
.ai-answer-md :deep(td) {
  border: 1px solid rgba(127, 127, 127, 0.4);
  padding: 6px 10px;
  text-align: left;
}
.ai-answer-md :deep(th) {
  background: rgba(127, 127, 127, 0.1);
  font-weight: 600;
}
.ai-answer-md :deep(blockquote) {
  border-left: 3px solid rgba(127, 127, 127, 0.4);
  padding-left: 10px;
  margin: 6px 0;
  color: rgba(0, 0, 0, 0.7);
}
.ai-answer-md :deep(a) {
  color: rgb(var(--v-theme-primary));
  text-decoration: underline;
}
</style>