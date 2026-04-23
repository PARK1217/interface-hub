<template>
  <div>
    <h2 class="text-h5 mb-4">AI 분석 어시스턴트</h2>
    <v-alert v-if="!hasKey" type="info" variant="tonal" class="mb-4">
      OPENAI_API_KEY 미설정 시 503 응답이 반환됩니다. <code>backend/.env</code> 에서 키를 설정한 후 사용하세요.
    </v-alert>

    <v-card class="mb-4">
      <v-card-text>
        <v-textarea
          v-model="question"
          label="장애 상황을 자연어로 설명하세요"
          rows="3"
          placeholder="예) 보험개발원 연동 API 가 오늘 오후 3시 이후 응답 지연 중이야. 원인 분석해줘."
        />
        <div class="d-flex justify-end">
          <v-btn color="primary" @click="ask" :loading="loading" :disabled="!question.trim()">
            <v-icon start icon="mdi-robot" /> 분석 요청
          </v-btn>
        </div>
      </v-card-text>
    </v-card>

    <v-card v-if="answer" class="mb-4">
      <v-card-title>RAG 응답</v-card-title>
      <v-card-text style="white-space: pre-wrap">{{ answer }}</v-card-text>
    </v-card>

    <v-card v-if="cases.length">
      <v-card-title>유사 사례 Top-{{ cases.length }}</v-card-title>
      <v-list>
        <v-list-item v-for="c in cases" :key="c.incident_id">
          <v-list-item-title>#{{ c.incident_id }} · {{ c.type }} · score={{ c.score.toFixed(3) }}</v-list-item-title>
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

const question = ref('');
const answer = ref('');
const cases = ref<{ incident_id: number; type: string; content: string; score: number }[]>([]);
const loading = ref(false);
const hasKey = ref(true); // surfaced via 503 from backend if missing
const snack = reactive({ show: false, text: '', color: 'error' });

async function ask() {
  loading.value = true;
  try {
    const res = await AI.ask(question.value);
    answer.value = res.data.answer;
    cases.value = res.data.similar_cases;
    hasKey.value = true;
  } catch (e: any) {
    if (e?.response?.status === 503) hasKey.value = false;
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