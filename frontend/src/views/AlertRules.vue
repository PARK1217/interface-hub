<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">알림 룰 (전역)</h2>
      <v-chip class="ml-3" size="small" color="primary" variant="tonal" prepend-icon="mdi-shield-cog-outline">
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
      여기서 설정한 룰은 <strong>전역</strong>으로 적용됩니다. 인터페이스별 음소거 와는
      별개이며, <strong>두 룰 모두 통과</strong>해야 알림이 발송됩니다 (AND).
      예) 인터페이스가 slack 만 허용 + 전역 warning 이 in_app+slack → 실제 발송 채널은 slack 만.
    </v-alert>

    <v-row v-if="rule">
      <!-- severity 별 채널 라우팅 -->
      <v-col cols="12" md="7">
        <v-card>
          <v-card-title>
            <v-icon icon="mdi-traffic-light-outline" class="mr-2" />
            severity 별 채널 라우팅
          </v-card-title>
          <v-card-subtitle>
            장애 심각도(severity) 에 따라 어느 채널로 알림을 보낼지. critical 은 보통 모든 채널,
            info 는 in_app(대시보드 뱃지) 만 권장.
          </v-card-subtitle>
          <v-card-text>
            <v-table density="comfortable">
              <thead>
                <tr>
                  <th style="width: 110px">severity</th>
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
                    <div class="d-flex" style="gap: 14px; flex-wrap: wrap">
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

      <!-- 근무시간 외 silence -->
      <v-col cols="12" md="5">
        <v-card>
          <v-card-title>
            <v-icon icon="mdi-bell-sleep-outline" class="mr-2" />
            근무시간 외 silence
          </v-card-title>
          <v-card-subtitle>
            지정된 시간대 / 주말에 알림 폭주 방지. critical 만 별도로 깨우는 옵션 권장 (기본 ON).
          </v-card-subtitle>
          <v-card-text>
            <v-switch
              v-model="rule.quiet_hours_enabled"
              label="시간대 silence 활성"
              color="primary"
              density="comfortable"
              hide-details
              class="mb-2"
            />
            <v-row dense :class="rule.quiet_hours_enabled ? '' : 'text-medium-emphasis'">
              <v-col cols="6">
                <v-text-field
                  v-model.number="rule.quiet_hours_start"
                  label="silence 시작 (시)"
                  type="number"
                  min="0"
                  max="23"
                  density="comfortable"
                  variant="outlined"
                  :disabled="!rule.quiet_hours_enabled"
                />
              </v-col>
              <v-col cols="6">
                <v-text-field
                  v-model.number="rule.quiet_hours_end"
                  label="silence 종료 (시)"
                  type="number"
                  min="0"
                  max="23"
                  density="comfortable"
                  variant="outlined"
                  :disabled="!rule.quiet_hours_enabled"
                />
              </v-col>
            </v-row>
            <div class="text-caption text-medium-emphasis mb-2">
              KST 24시 기준. start &gt; end 면 자정 넘김 (예: 22 → 8 = 22시~익일 8시).
              현재 설정: <strong>{{ quietHoursPreview }}</strong>
            </div>

            <v-divider class="my-3" />

            <v-switch
              v-model="rule.quiet_hours_skip_critical"
              label="critical 은 silence 무시 (권장 ON)"
              color="error"
              density="comfortable"
              hide-details
              class="mb-2"
              :disabled="!rule.quiet_hours_enabled && !rule.weekend_silence"
            />
            <v-switch
              v-model="rule.weekend_silence"
              label="주말 (토/일) 종일 silence"
              color="amber-darken-2"
              density="comfortable"
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
        현재 설정 시뮬레이션
      </v-card-title>
      <v-card-text>
        <div class="text-caption text-medium-emphasis mb-2">
          지금 (KST {{ nowLabel }}) 이 시간에 인터페이스가 모든 채널 (in_app/slack/email) 허용 상태로
          장애를 일으켰다면 어느 채널이 실제 발송될지.
        </div>
        <v-table density="compact">
          <thead>
            <tr>
              <th>severity</th>
              <th>전역 룰 채널</th>
              <th>silence?</th>
              <th>실제 발송</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in simRows" :key="row.severity">
              <td>
                <v-chip size="x-small" :color="row.color" variant="flat">{{ row.severity }}</v-chip>
              </td>
              <td>
                <span class="text-caption">{{ row.allowed.join(', ') || '(없음)' }}</span>
              </td>
              <td>
                <v-chip v-if="row.silenced" size="x-small" color="grey" variant="flat" prepend-icon="mdi-bell-off-outline">
                  {{ row.silenceReason }}
                </v-chip>
                <span v-else class="text-caption text-success">정상</span>
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
                  {{ ch }}
                </v-chip>
                <span v-if="!row.effective.length" class="text-caption text-medium-emphasis">— skip —</span>
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
  { value: 'in_app', label: '인앱 (대시보드 뱃지·toast)' },
  { value: 'slack', label: 'Slack' },
  { value: 'email', label: 'Email' },
];

const severityRows = [
  { key: 'info_channels', label: 'info', icon: 'mdi-information-outline', color: 'info' },
  { key: 'warning_channels', label: 'warning', icon: 'mdi-alert-outline', color: 'warning' },
  { key: 'critical_channels', label: 'critical', icon: 'mdi-fire', color: 'error' },
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
    if (inWeekend) { silenced = true; reason = '주말 silence'; }
    else if (inQuietHours) { silenced = true; reason = '근무시간 외'; }
    if (silenced && sr.label === 'critical' && rule.value!.quiet_hours_skip_critical) {
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
