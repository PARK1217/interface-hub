<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">알림 룰 (전역)</h2>
      <v-chip class="ml-3" size="small" color="primary" variant="tonal">
        전역 정책
      </v-chip>
      <v-spacer />
      <v-btn
        v-if="dirty"
        color="primary"
        variant="elevated"
        prepend-icon="mdi-content-save-outline"
        :loading="saving"
        @click="save"
      >
        저장
      </v-btn>
      <v-btn
        v-if="dirty"
        variant="text"
        prepend-icon="mdi-undo"
        class="ml-2"
        @click="reload"
      >
        되돌리기
      </v-btn>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <strong>전역 공통 정책</strong> — 인터페이스별 음소거와 모두 통과해야 알림이 발송됩니다.
    </v-alert>

    <v-row v-if="rule">
      <!-- 심각도별 발송 채널 -->
      <v-col cols="12" md="6">
        <v-card height="100%">
          <v-card-title>
            <v-icon icon="mdi-traffic-light-outline" class="mr-2" />
            심각도별 발송 채널
          </v-card-title>
          <v-card-subtitle>
            장애 심각도에 따라 어느 채널로 알림을 보낼지. 긴급은 보통 모든 채널,
            참고는 인앱(대시보드 뱃지)만 권장합니다.
          </v-card-subtitle>
          <v-card-text>
            <v-table density="comfortable">
              <thead>
                <tr>
                  <th style="width: 110px">심각도</th>
                  <th>발송 채널</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in severityRows" :key="row.key">
                  <td>
                    <v-chip
                      size="small"
                      :color="row.color"
                      variant="flat"
                      :prepend-icon="row.icon"
                    >
                      {{ row.label }}
                    </v-chip>
                  </td>
                  <td>
                    <div class="severity-channel-cell d-flex flex-wrap">
                      <v-checkbox
                        v-for="ch in channelOptions"
                        :key="ch.value"
                        :model-value="((rule as any)[row.key] as string[]).includes(ch.value)"
                        :label="ch.label"
                        hide-details
                        density="compact"
                        :color="row.color"
                        @update:model-value="(v) => toggleChannel(row.key, ch.value, !!v)"
                      />
                    </div>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- 야간/주말 알림 끄기 -->
      <v-col cols="12" md="6">
        <v-card height="100%">
          <v-card-title>
            <v-icon icon="mdi-bell-sleep-outline" class="mr-2" />
            야간/주말 알림 끄기
          </v-card-title>
          <v-card-subtitle>
            지정된 시간대 / 주말에는 알림을 보내지 않습니다. 긴급 단계만 별도로 깨우는
            옵션을 권장합니다 (기본 켜짐).
          </v-card-subtitle>
          <v-card-text class="quiet-hours-card">
            <v-switch
              v-model="rule.quiet_hours_enabled"
              label="시간대 알림 끄기 사용"
              color="primary"
              density="compact"
              hide-details
              class="mb-3"
            />
            <v-row dense :class="rule.quiet_hours_enabled ? '' : 'text-medium-emphasis'">
              <v-col cols="6">
                <v-text-field
                  v-model.number="rule.quiet_hours_start"
                  label="알림 끄기 시작 (시)"
                  type="number"
                  min="0"
                  max="23"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :disabled="!rule.quiet_hours_enabled"
                />
              </v-col>
              <v-col cols="6">
                <v-text-field
                  v-model.number="rule.quiet_hours_end"
                  label="알림 끄기 종료 (시)"
                  type="number"
                  min="0"
                  max="23"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :disabled="!rule.quiet_hours_enabled"
                />
              </v-col>
            </v-row>
            <div class="text-caption text-medium-emphasis mt-2">
              한국 시간(0~23시) 기준. 시작 시각이 종료 시각보다 크면 자정을 넘어갑니다
              (예: 22 → 8 = 밤 10시 ~ 다음 날 아침 8시).
              현재 설정: <strong>{{ quietHoursPreview }}</strong>
            </div>

            <v-divider class="my-2" />

            <v-switch
              v-model="rule.quiet_hours_skip_critical"
              label="긴급 알림은 끄기 시간에도 보냄 (권장)"
              color="error"
              density="compact"
              hide-details
              :disabled="!rule.quiet_hours_enabled && !rule.weekend_silence"
            />
            <v-switch
              v-model="rule.weekend_silence"
              label="주말 (토/일) 종일 알림 끄기"
              color="amber-darken-2"
              density="compact"
              hide-details
            />
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 시뮬레이션 — 현재 설정으로 가상 알림 평가 -->
    <v-card v-if="rule" class="mt-4">
      <v-card-title>
        <v-icon icon="mdi-flask-outline" class="mr-2" />
        현재 설정 미리 보기
      </v-card-title>
      <v-card-text>
        <div class="text-caption text-medium-emphasis mb-2">
          지금 시각 (한국 시간 {{ nowLabel }}) 에 어떤 인터페이스가 모든 채널을 허용한 상태로
          장애를 일으켰다면, 위 정책에 따라 실제로 어느 채널에 알림이 발송될지를 보여줍니다.
        </div>
        <v-table density="compact">
          <thead>
            <tr>
              <th>심각도</th>
              <th>정책상 발송 채널</th>
              <th>현재 발송 여부</th>
              <th>실제 발송</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in simRows" :key="row.severity">
              <td>
                <v-chip size="x-small" :color="row.color" variant="flat">{{ row.severity }}</v-chip>
              </td>
              <td>
                <span class="text-caption">{{ row.allowed.map(channelLabel).join(', ') || '(없음)' }}</span>
              </td>
              <td>
                <v-chip v-if="row.silenced" size="x-small" color="grey" variant="flat" prepend-icon="mdi-bell-off-outline">
                  {{ row.silenceReason }}
                </v-chip>
                <v-chip v-else size="x-small" color="success" variant="tonal" prepend-icon="mdi-bell-ring-outline">
                  발송
                </v-chip>
              </td>
              <td>
                <v-chip
                  v-for="ch in row.effective"
                  :key="ch"
                  size="x-small"
                  color="primary"
                  variant="tonal"
                  class="mr-1"
                >
                  {{ channelLabel(ch) }}
                </v-chip>
                <span v-if="!row.effective.length" class="text-caption text-medium-emphasis">— 보내지 않음 —</span>
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>

    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2500">{{ snack.text }}</v-snackbar>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { AlertRules, type AlertChannel, type AlertRulePayload } from '@/api/client';

const rule = ref<AlertRulePayload | null>(null);
const original = ref<string>('');  // JSON 직렬화로 dirty 비교
const saving = ref(false);
const snack = reactive({ show: false, text: '', color: 'success' });

const channelOptions: { value: AlertChannel; label: string }[] = [
  { value: 'in_app', label: '인앱 알림 (대시보드 뱃지·팝업)' },
  { value: 'slack', label: 'Slack' },
  { value: 'email', label: '이메일' },
];

function channelLabel(ch: string): string {
  return ({ in_app: '인앱', slack: 'Slack', email: '이메일' } as Record<string, string>)[ch] ?? ch;
}

const severityRows = [
  { key: 'info_channels', label: '참고', icon: 'mdi-information-outline', color: 'info' },
  { key: 'warning_channels', label: '주의', icon: 'mdi-alert-outline', color: 'warning' },
  { key: 'critical_channels', label: '긴급', icon: 'mdi-fire', color: 'error' },
];

function notify(text: string, color: 'success' | 'error' | 'warning' = 'success') {
  snack.text = text;
  snack.color = color;
  snack.show = true;
}

async function reload() {
  const res = await AlertRules.get();
  rule.value = res.data;
  original.value = JSON.stringify(res.data);
}

const dirty = computed(() => rule.value && JSON.stringify(rule.value) !== original.value);

function toggleChannel(field: string, ch: AlertChannel, on: boolean) {
  if (!rule.value) return;
  const arr = (rule.value as any)[field] as AlertChannel[];
  const next = on ? [...new Set([...arr, ch])] : arr.filter((c) => c !== ch);
  (rule.value as any)[field] = next;
}

async function save() {
  if (!rule.value) return;
  saving.value = true;
  try {
    const res = await AlertRules.update({
      info_channels: rule.value.info_channels,
      warning_channels: rule.value.warning_channels,
      critical_channels: rule.value.critical_channels,
      quiet_hours_enabled: rule.value.quiet_hours_enabled,
      quiet_hours_start: rule.value.quiet_hours_start,
      quiet_hours_end: rule.value.quiet_hours_end,
      quiet_hours_skip_critical: rule.value.quiet_hours_skip_critical,
      weekend_silence: rule.value.weekend_silence,
    });
    rule.value = res.data;
    original.value = JSON.stringify(res.data);
    notify('저장 완료');
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '저장 실패', 'error');
  } finally {
    saving.value = false;
  }
}

// silence 시뮬레이션 — 백엔드 evaluate_global_rule 과 동일 로직 (프론트 미리보기용).
// 실제 발송 결정은 백엔드가 하므로 이건 UI 가이드일 뿐.
const now = ref(new Date());
const nowTimer = setInterval(() => { now.value = new Date(); }, 30_000);
onMounted(reload);

const nowLabel = computed(() =>
  now.value.toLocaleString('ko-KR', { hour12: false, dateStyle: 'short', timeStyle: 'short' })
);

function withinQuiet(start: number, end: number, hour: number): boolean {
  if (start === end) return false;
  if (start < end) return start <= hour && hour < end;
  return hour >= start || hour < end;
}

const quietHoursPreview = computed(() => {
  if (!rule.value || !rule.value.quiet_hours_enabled) return '비활성';
  const s = rule.value.quiet_hours_start;
  const e = rule.value.quiet_hours_end;
  const fmt = (h: number) => `${String(h).padStart(2, '0')}:00`;
  if (s < e) return `매일 ${fmt(s)} ~ ${fmt(e)}`;
  return `매일 ${fmt(s)} ~ 익일 ${fmt(e)}`;
});

const simRows = computed(() => {
  if (!rule.value) return [];
  const hour = now.value.getHours();
  const isWeekend = [0, 6].includes(now.value.getDay());
  const inQuietHours = rule.value.quiet_hours_enabled
    && withinQuiet(rule.value.quiet_hours_start, rule.value.quiet_hours_end, hour);
  const inWeekend = rule.value.weekend_silence && isWeekend;

  return severityRows.map((sr) => {
    const allowed = (rule.value as any)[sr.key] as AlertChannel[];
    let silenced = false;
    let reason = '';
    if (inWeekend) { silenced = true; reason = '주말 차단'; }
    else if (inQuietHours) { silenced = true; reason = '야간 차단'; }
    // 긴급 단계는 "끄기 시간에도 보냄" 옵션이 켜져 있으면 silence 무시
    if (silenced && sr.label === '긴급' && rule.value!.quiet_hours_skip_critical) {
      silenced = false;
      reason = '';
    }
    return {
      severity: sr.label,
      color: sr.color,
      allowed,
      silenced,
      silenceReason: reason,
      effective: silenced ? [] : allowed,
    };
  });
});

import { onBeforeUnmount } from 'vue';
onBeforeUnmount(() => clearInterval(nowTimer));
</script>

<style scoped>
/* severity 별 채널 체크박스 — wrap 시 행 간격 너무 벌어지는 문제 해결.
   v-checkbox 의 기본 min-height 가 40px+ 라 좁은 화면에서 두 줄로 wrap 되면
   체크박스 사이 빈 공간이 크게 느껴짐. column-gap 14px / row-gap 4px 로
   분리하고 v-checkbox 자체 min-height 도 28px 로 압축. */
.severity-channel-cell {
  column-gap: 14px;
  row-gap: 4px;
}
.severity-channel-cell :deep(.v-checkbox),
.severity-channel-cell :deep(.v-selection-control) {
  min-height: 28px;
}
.severity-channel-cell :deep(.v-selection-control__wrapper) {
  height: 28px;
}

/* 야간/주말 알림 끄기 카드 — 좌측 심각도 카드와 세로 길이 맞추기 위해
   v-switch 의 기본 min-height 압축. */
.quiet-hours-card :deep(.v-switch),
.quiet-hours-card :deep(.v-selection-control) {
  min-height: 32px;
}
.quiet-hours-card :deep(.v-selection-control__wrapper) {
  height: 32px;
}
</style>
