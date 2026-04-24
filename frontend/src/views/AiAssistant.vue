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

    <!-- LLM 호출 실패 사유 (Phase B.7) — provider/status/message + 본문 발췌 -->
    <v-alert
      v-else-if="lastMode === 'fallback' && llmError"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-4"
    >
      <div class="d-flex align-start">
        <v-icon icon="mdi-robot-confused-outline" class="mr-2 mt-1" />
        <div style="flex: 1">
          <div class="text-subtitle-2">LLM 호출 실패 — fallback 응답으로 대체</div>
          <div class="text-body-2 mt-1">
            <strong>{{ llmError.provider }}</strong>
            <v-chip
              v-if="llmError.status"
              size="x-small"
              variant="flat"
              :color="llmError.status >= 500 ? 'error' : 'warning'"
              class="mx-2"
            >
              HTTP {{ llmError.status }}
            </v-chip>
            <v-chip
              v-else
              size="x-small"
              variant="flat"
              color="grey-darken-1"
              class="mx-2"
            >
              {{ kindLabel(llmError.kind) }}
            </v-chip>
            {{ llmError.message }}
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

            <!-- Phase B.8.5 — 동적 prompt 추천. popular > incident > interface 템플릿 자동 분기 -->
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

        <v-card v-if="answer" class="mb-4">
          <v-card-title class="d-flex align-center">
            <span>분석 결과</span>
            <v-chip
              v-if="lastMode"
              class="ml-3"
              size="x-small"
              :color="lastMode === 'llm' ? 'success' : 'info'"
            >
              {{ lastMode }}{{ lastProvider ? ` · ${lastProvider}` : '' }}
            </v-chip>
            <v-chip
              v-if="lastCached"
              class="ml-2"
              size="x-small"
              color="amber-darken-2"
              variant="flat"
              prepend-icon="mdi-flash"
              title="Redis 캐시 hit — LLM 호출 없이 즉시 응답"
            >
              cached
            </v-chip>
          </v-card-title>
          <v-card-text style="white-space: pre-wrap">{{ answer }}</v-card-text>
        </v-card>

        <v-card v-if="cases.length">
          <v-card-title>유사 사례 Top-{{ cases.length }}</v-card-title>
          <v-list>
            <v-list-item v-for="c in cases" :key="c.incident_id">
              <v-list-item-title>
                #{{ c.incident_id }} · {{ c.type }} ·
                <v-chip size="x-small" variant="tonal" :color="scoreColor(c.score)">
                  유사도 {{ (c.score * 100).toFixed(1) }}%
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle style="white-space: pre-wrap">{{ c.content }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- Phase B.8.6 — 본인 대화 히스토리 사이드 패널 -->
      <v-col v-if="showHistory" cols="4">
        <v-card variant="flat" border>
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-history" class="mr-2" />
            <span class="text-subtitle-1">내 질의 이력</span>
            <v-spacer />
            <v-btn icon="mdi-refresh" size="x-small" variant="text" @click="loadHistory" />
          </v-card-title>
          <v-card-text v-if="!history.length" class="text-center text-medium-emphasis">
            아직 이력이 없습니다. 질문을 하면 여기에 쌓입니다.
          </v-card-text>
          <v-list v-else density="compact" max-height="600" style="overflow-y: auto">
            <v-list-item
              v-for="h in history"
              :key="h.id"
              @click="question = h.question"
              :title="h.question.length > 60 ? h.question.slice(0, 60) + '…' : h.question"
            >
              <template #title>
                <div class="text-body-2" style="white-space: normal">
                  {{ h.question.length > 60 ? h.question.slice(0, 60) + '…' : h.question }}
                </div>
              </template>
              <template #subtitle>
                <div class="d-flex align-center" style="gap: 4px; flex-wrap: wrap">
                  <v-chip
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
import { onMounted, reactive, ref } from 'vue';
import { AI, type AiHistoryItem, type AiSuggestion } from '@/api/client';
import { formatDateTimeShort } from '@/utils/format';

interface LLMErrorPayload {
  kind: string;
  provider: string;
  message: string;
  status?: number | null;
  body_excerpt?: string | null;
}

const question = ref('');
const answer = ref('');
const cases = ref<{ incident_id: number; type: string; content: string; score: number }[]>([]);
const lastMode = ref<'llm' | 'fallback' | null>(null);
const lastProvider = ref<string | null>(null);
const lastCached = ref(false);
const llmError = ref<LLMErrorPayload | null>(null);
const loading = ref(false);
const snack = reactive({ show: false, text: '', color: 'error' });

const aiStatus = ref<{ configured: boolean; provider: string; model: string | null } | null>(null);
const suggestions = ref<AiSuggestion[]>([]);
const history = ref<AiHistoryItem[]>([]);
const showHistory = ref(false);

onMounted(async () => {
  try {
    aiStatus.value = (await AI.status()).data;
  } catch {
    /* ignore */
  }
  await loadSuggestions();
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
    history.value = (await AI.myHistory(20)).data;
  } catch {
    history.value = [];
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
    case 'timeout': return '응답 시간 초과';
    case 'network': return '네트워크 오류';
    case 'parse_error': return '응답 파싱 실패';
    case 'not_configured': return '설정 누락';
    case 'unknown': return '알 수 없는 오류';
    case 'http_error': return 'HTTP 오류';
    default: return kind;
  }
}

function describeAxiosError(e: any): string {
  if (!e) return 'AI 분석 실패 (알 수 없는 원인)';
  const detail = e?.response?.data?.detail;
  if (detail) return detail;
  const status = e?.response?.status;
  if (status) return `백엔드 오류 HTTP ${status}`;
  if (e?.code === 'ECONNABORTED') return '백엔드 응답 시간 초과 (15초)';
  if (e?.code === 'ERR_NETWORK') return '백엔드 네트워크 오류 — 서버 가동 상태 확인';
  return e?.message || 'AI 분석 실패';
}

async function ask() {
  loading.value = true;
  try {
    const res = await AI.ask(question.value);
    answer.value = res.data.answer;
    cases.value = res.data.similar_cases;
    lastMode.value = (res.data.mode as 'llm' | 'fallback') ?? 'llm';
    lastProvider.value = res.data.provider ?? null;
    lastCached.value = !!res.data.cached;
    llmError.value = (res.data.llm_error as LLMErrorPayload | null) ?? null;
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
  }
}
</script>