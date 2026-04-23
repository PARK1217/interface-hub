<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">AI 분석 어시스턴트</h2>
      <v-spacer />
      <v-chip
        v-if="lastMode"
        :color="lastMode === 'llm' ? 'success' : 'info'"
        variant="tonal"
        size="small"
        :prepend-icon="lastMode === 'llm' ? 'mdi-robot-happy' : 'mdi-text-search'"
      >
        {{ lastMode === 'llm' ? 'LLM 모드 (OpenAI)' : 'Fallback 모드 (TF-IDF)' }}
      </v-chip>
    </div>

    <v-alert v-if="lastMode === 'fallback'" type="info" variant="tonal" density="compact" class="mb-4">
      OpenAI 키 미설정 → 키워드 매칭(TF-IDF)으로 과거 장애 사례를 검색해 답변합니다.
      <code>backend/.env</code> 의 <code>OPENAI_API_KEY</code> 를 채우면 자동으로 LLM 모드로 전환됩니다.
    </v-alert>

    <v-card class="mb-4">
      <v-card-text>
        <v-textarea
          v-model="question"
          label="장애 상황을 자연어로 설명하세요"
          rows="3"
          placeholder="예) 보험개발원 연동 API 가 오늘 오후 3시 이후 응답 지연 중이야. 원인 분석해줘."
        />
        <div class="d-flex align-center">
          <v-chip-group
            v-model="exampleIdx"
            mandatory
            selected-class="text-primary"
            density="compact"
          >
            <v-chip
              v-for="(ex, i) in examples"
              :key="i"
              size="small"
              variant="tonal"
              @click="question = ex"
            >
              {{ ex.slice(0, 28) }}…
            </v-chip>
          </v-chip-group>
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
          {{ lastMode }}
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

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="3500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { AI } from '@/api/client';

const examples = [
  '보험개발원 연동 API 가 오늘 오후 3시 이후 응답 지연 중이야. 원인 분석해줘.',
  '신용정보원 CB조회가 갑자기 401 다발로 막혔어. 어떻게 처리하지?',
  '카카오 알림톡 발송이 422 에러로 실패하고 있어. 변경 사항 있었나?',
  '토스페이먼츠 자동이체 정기 출금일에 실패율이 12%까지 올라갔어.',
];
const exampleIdx = ref(0);
const question = ref('');
const answer = ref('');
const cases = ref<{ incident_id: number; type: string; content: string; score: number }[]>([]);
const lastMode = ref<'llm' | 'fallback' | null>(null);
const loading = ref(false);
const snack = reactive({ show: false, text: '', color: 'error' });

function scoreColor(s: number) {
  if (s > 0.5) return 'success';
  if (s > 0.2) return 'warning';
  return 'grey';
}

async function ask() {
  loading.value = true;
  try {
    const res = await AI.ask(question.value);
    answer.value = res.data.answer;
    cases.value = res.data.similar_cases;
    lastMode.value = (res.data.mode as 'llm' | 'fallback') ?? 'llm';
  } catch (e: any) {
    Object.assign(snack, {
      show: true,
      text: e?.response?.data?.detail ?? 'AI 분석 실패',
      color: 'error',
    });
  } finally {
    loading.value = false;
  }
}
</script>