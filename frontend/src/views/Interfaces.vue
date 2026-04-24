<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h2 class="text-h5">인터페이스 관리</h2>
      <v-chip
        class="ml-3"
        size="small"
        color="primary"
        variant="tonal"
        prepend-icon="mdi-format-list-bulleted"
      >
        총 {{ rows.length }}개
      </v-chip>
      <v-spacer />
      <v-btn
        v-if="auth.isAdmin"
        color="primary"
        prepend-icon="mdi-plus"
        @click="openCreate"
      >
        새 인터페이스
      </v-btn>
      <v-chip v-else size="small" variant="tonal" color="grey">
        {{ auth.role }} 권한 — 읽기 전용
      </v-chip>
    </div>

    <v-card class="mb-4">
      <v-card-text>
        <v-row dense align="center">
          <v-col cols="12" md="3">
            <v-select
              v-model="filter.category"
              :items="categoryFilterOptions"
              item-title="label"
              item-value="value"
              label="분류 (내부/외부)"
              clearable
              density="compact"
              hide-details
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="2">
            <v-select
              v-model="filter.protocol"
              :items="protocolOptions"
              label="프로토콜"
              clearable
              density="compact"
              hide-details
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="3">
            <v-select
              v-model="filter.organization"
              :items="organizationOptions"
              label="기관"
              clearable
              density="compact"
              hide-details
              @update:model-value="load"
            />
          </v-col>
          <v-col cols="12" md="4" class="d-flex align-center" style="gap: 16px; padding-left: 16px">
            <v-switch
              v-model="filter.enabledOnly"
              label="활성만"
              hide-details
              density="compact"
              color="primary"
              :disabled="filter.trashOnly"
              @update:model-value="load"
            />
            <v-switch
              v-model="filter.trashOnly"
              label="휴지통"
              hide-details
              density="compact"
              color="grey"
              @update:model-value="load"
            />
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="focusedId"
      :type="focusedRowExists ? 'info' : 'warning'"
      variant="tonal"
      density="compact"
      class="mb-3"
      closable
      @click:close="focusedId = null"
    >
      <template v-if="focusedRowExists">
        인터페이스 <strong>#{{ focusedId }}</strong> 가 목록에서 파란 테두리로 강조되어 있습니다.
      </template>
      <template v-else>
        인터페이스 <strong>#{{ focusedId }}</strong> 를 찾지 못했습니다 — 보관 처리됐거나 삭제됐을 수 있습니다.
      </template>
    </v-alert>

    <v-card>
      <v-alert
        v-if="filter.trashOnly"
        type="info"
        variant="tonal"
        density="compact"
        class="mx-3 mt-3"
      >
        보관 처리된 인터페이스 입니다. 호출 로그·장애 이력·SLA 목표는
        <strong>감사 대응을 위해 영구 보존</strong>되며, 자동 삭제되지 않습니다.
        다시 사용하려면 우측 <strong>복원</strong> 버튼을 누르세요.
      </v-alert>
      <v-data-table
        :headers="headers"
        :items="rows"
        :loading="loading"
        item-value="id"
        density="comfortable"
        :row-props="rowProps"
      >
        <template #item.direction="{ item }">
          <v-chip
            v-if="item.direction === 'INBOUND'"
            size="x-small"
            color="deep-purple"
            variant="flat"
            prepend-icon="mdi-arrow-left-bold-outline"
            title="INBOUND — 외부가 우리를 호출 (webhook/콜백, ingest API 로 적재)"
          >
            IN
          </v-chip>
          <v-chip
            v-else
            size="x-small"
            color="primary"
            variant="tonal"
            prepend-icon="mdi-arrow-right-bold-outline"
            title="OUTBOUND — 우리가 외부 호출 (스케줄/수동 실행)"
          >
            OUT
          </v-chip>
        </template>
        <template #item.category="{ item }">
          <v-chip
            size="small"
            :color="categoryMeta(item.category).color"
            variant="tonal"
            :prepend-icon="categoryMeta(item.category).icon"
            :title="categoryMeta(item.category).desc"
          >
            {{ categoryMeta(item.category).label }}
          </v-chip>
        </template>
        <template #item.protocol="{ item }">
          <v-chip size="small" :color="protocolColor(item.protocol)">{{ item.protocol }}</v-chip>
        </template>
        <template #item.schedule_cron="{ item }">
          <v-chip
            size="small"
            variant="tonal"
            :color="cronToLabel(item.schedule_cron).color"
            :prepend-icon="cronToLabel(item.schedule_cron).icon"
          >
            {{ cronToLabel(item.schedule_cron).label }}
          </v-chip>
        </template>
        <template #item.enabled="{ item }">
          <div class="d-flex align-center" style="gap: 4px; flex-wrap: wrap">
            <v-chip
              v-if="item.deleted_at"
              size="small"
              color="grey"
              variant="tonal"
              prepend-icon="mdi-archive-outline"
            >
              보관됨
            </v-chip>
            <v-chip
              v-else-if="item.enabled"
              size="small"
              color="success"
              variant="tonal"
              prepend-icon="mdi-circle-medium"
            >
              활성
            </v-chip>
            <v-chip
              v-else
              size="small"
              color="warning"
              variant="tonal"
              prepend-icon="mdi-pause-circle-outline"
            >
              일시중지
            </v-chip>
            <!-- 음소거 칩 — 남은 시간 표시 + 즉시 해제 -->
            <v-chip
              v-if="isMuted(item)"
              size="x-small"
              color="grey-darken-2"
              variant="flat"
              prepend-icon="mdi-bell-off-outline"
              :title="`${formatDateTimeShort(item.muted_until)} 까지 음소거 (toast/Slack/Email skip, 기록은 유지)`"
            >
              음소거 {{ muteRemaining(item) }}
            </v-chip>
            <!-- 인증 키 만료 임박 칩 — D-7 이내 노랑, D-3 이하 빨강, 만료된 것 회색 -->
            <v-chip
              v-if="expiryStatus(item)"
              size="x-small"
              :color="expiryStatus(item)!.color"
              variant="flat"
              prepend-icon="mdi-key-alert-outline"
              :title="expiryStatus(item)!.title"
            >
              {{ expiryStatus(item)!.label }}
            </v-chip>
          </div>
        </template>
        <template #item.actions="{ item }">
          <template v-if="!item.deleted_at">
            <!-- 🔓 시크릿 조회: ADMIN + has_secret 만. 맨 앞에 두어 다른 액션 정렬을 흐트리지 않음 -->
            <v-btn
              v-if="auth.isAdmin && item.has_secret"
              icon="mdi-key-outline"
              variant="text"
              size="small"
              color="warning"
              title="시크릿 조회 (사유 기록됨)"
              @click="openReveal(item)"
            />
            <!-- ▶ 실행: OPERATOR 이상. INBOUND 는 능동 실행 불가라 비활성. -->
            <v-btn
              v-if="auth.canMutate"
              icon="mdi-play"
              variant="text"
              size="small"
              :loading="running === item.id"
              :disabled="item.direction === 'INBOUND'"
              :title="item.direction === 'INBOUND' ? 'INBOUND 인터페이스는 외부가 우리를 호출하는 구조라 능동 실행 불가 (ingest API 사용)' : '실행'"
              @click="run(item)"
            />
            <!-- 🔕 음소거: OPERATOR 이상 -->
            <v-menu v-if="auth.canMutate" location="bottom end">
              <template #activator="{ props }">
                <v-btn
                  v-bind="props"
                  :icon="isMuted(item) ? 'mdi-bell-off' : 'mdi-bell-outline'"
                  variant="text"
                  size="small"
                  :color="isMuted(item) ? 'grey-darken-2' : ''"
                  :title="isMuted(item) ? '음소거 중 — 클릭해 해제 또는 시간 변경' : '알림 음소거 (정기 점검 시간 등)'"
                />
              </template>
              <v-list density="compact" min-width="180">
                <v-list-subheader>음소거 시간</v-list-subheader>
                <v-list-item
                  v-for="m in muteOptions"
                  :key="m.minutes"
                  :title="m.label"
                  @click="mute(item, m.minutes)"
                />
                <v-divider v-if="isMuted(item)" />
                <v-list-item
                  v-if="isMuted(item)"
                  prepend-icon="mdi-bell-ring-outline"
                  title="즉시 해제"
                  @click="unmute(item)"
                />
              </v-list>
            </v-menu>
            <!-- ✏️ 수정 / 📦 보관: ADMIN 만 -->
            <v-btn
              v-if="auth.isAdmin"
              icon="mdi-pencil"
              variant="text"
              size="small"
              title="수정"
              @click="openEdit(item)"
            />
            <v-btn
              v-if="auth.isAdmin"
              icon="mdi-archive-arrow-down"
              variant="text"
              size="small"
              color="error"
              title="보관 처리"
              @click="remove(item)"
            />
          </template>
          <template v-else>
            <v-btn
              v-if="auth.isAdmin"
              prepend-icon="mdi-restore"
              variant="flat"
              color="success"
              size="small"
              @click="restore(item)"
            >
              복원
            </v-btn>
          </template>
        </template>
      </v-data-table>
    </v-card>

    <v-dialog v-model="dialog" max-width="720" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center pa-4">
          <v-icon
            :icon="form.id ? 'mdi-pencil-outline' : 'mdi-plus-circle-outline'"
            class="mr-2"
            color="primary"
          />
          <span>{{ form.id ? '인터페이스 수정' : '새 인터페이스 등록' }}</span>
          <v-spacer />
          <v-btn
            v-if="form.id && !form.deleted_at"
            size="small"
            variant="text"
            color="error"
            prepend-icon="mdi-archive-arrow-down-outline"
            @click="archiveFromDialog"
          >
            보관 처리
          </v-btn>
        </v-card-title>
        <v-divider />
        <v-card-text class="pa-5">
          <div class="text-overline text-medium-emphasis mb-2">기본 정보</div>
          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.name" label="이름 *" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.organization" label="기관" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="6">
              <v-select
                v-model="form.category"
                :items="categoryOptions"
                item-title="label"
                item-value="value"
                label="분류 *"
                density="comfortable"
                variant="outlined"
                hint="기획서 1번 항목 — 내부 핵심 시스템 / 외부 제휴사 / 외부 규제기관"
                persistent-hint
              >
                <template #item="{ props, item }">
                  <v-list-item v-bind="props">
                    <template #prepend>
                      <v-icon :icon="item.raw.icon" :color="item.raw.color" />
                    </template>
                    <template #subtitle>
                      <span class="text-caption">{{ item.raw.desc }}</span>
                    </template>
                  </v-list-item>
                </template>
              </v-select>
            </v-col>
          </v-row>

          <div class="text-overline text-medium-emphasis mb-2 mt-3">연결</div>
          <v-card variant="outlined" rounded="lg" class="pa-3 mb-3">
            <div class="text-caption text-medium-emphasis mb-2">호출 방향</div>
            <v-radio-group v-model="form.direction" hide-details inline density="compact">
              <v-radio value="OUTBOUND" color="primary">
                <template #label>
                  <v-icon icon="mdi-arrow-right-bold-outline" class="mr-1" />
                  <strong class="mr-1">OUTBOUND</strong>
                  <span class="text-caption text-medium-emphasis">— 우리가 외부 호출 (스케줄/수동 실행 가능)</span>
                </template>
              </v-radio>
              <v-radio value="INBOUND" color="deep-purple" class="ml-4">
                <template #label>
                  <v-icon icon="mdi-arrow-left-bold-outline" class="mr-1" />
                  <strong class="mr-1">INBOUND</strong>
                  <span class="text-caption text-medium-emphasis">— 외부가 우리 호출 (webhook/콜백 — ingest API 만)</span>
                </template>
              </v-radio>
            </v-radio-group>
          </v-card>
          <v-row dense>
            <v-col cols="6" md="3">
              <v-select v-model="form.protocol" :items="['REST', 'SOAP', 'FTP', 'MQ', 'BATCH']" label="프로토콜" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="6" md="3">
              <v-select v-model="form.method" :items="['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" label="메서드" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field v-model="form.endpoint" label="엔드포인트 URL *" density="comfortable" variant="outlined" />
            </v-col>
          </v-row>

          <!-- 스케줄 입력 — raw cron 직접 입력 폼 절대 두지 말 것.
               운영자가 cron 문법 (* / - , 등) 잘못 쳐서 깨지는 사고가 표준
               이슈. 라디오 버튼으로 6가지 패턴 (사용 안 함/매 N분/매시간/매일/
               매주/매월) 선택 → 드롭다운으로 시·분·일·요일만 입력 → 내부에서
               cron 표현식 자동 생성. 운영자에게 cron 문법 노출 0. -->
          <div class="text-overline text-medium-emphasis mb-2 mt-4">스케줄</div>
          <v-card variant="outlined" rounded="lg" class="pa-4 mb-2">
            <v-btn-toggle
              v-model="sched.type"
              mandatory
              density="comfortable"
              color="primary"
              variant="outlined"
              divided
              class="mb-3 d-flex flex-wrap"
              style="row-gap: 4px"
            >
              <v-btn
                v-for="t in scheduleTypes"
                :key="t.value"
                :value="t.value"
                size="small"
              >
                {{ t.label }}
              </v-btn>
            </v-btn-toggle>

            <v-row v-if="sched.type === 'minutes'" dense>
              <v-col cols="12" md="6">
                <v-select
                  v-model="sched.everyN"
                  :items="[5, 10, 15, 20, 30]"
                  label="N분 마다"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <v-row v-if="sched.type === 'hourly'" dense>
              <v-col cols="12" md="6">
                <v-select
                  v-model="sched.minute"
                  :items="minuteChoices"
                  label="매시 N분"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <v-row v-if="['daily','weekly','monthly'].includes(sched.type)" dense>
              <v-col cols="6" md="3">
                <v-select
                  v-model="sched.hour"
                  :items="hourChoices"
                  label="시"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
              <v-col cols="6" md="3">
                <v-select
                  v-model="sched.minute"
                  :items="minuteChoices"
                  label="분"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
              <v-col v-if="sched.type === 'monthly'" cols="12" md="6">
                <v-select
                  v-model="sched.day"
                  :items="dayChoices"
                  label="매월 N일"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>

            <div v-if="sched.type === 'weekly'" class="mt-3">
              <div class="text-caption text-medium-emphasis mb-2">요일 (복수 선택 가능)</div>
              <v-btn-toggle
                v-model="sched.weekdays"
                multiple
                color="primary"
                density="comfortable"
                variant="outlined"
                divided
              >
                <v-btn
                  v-for="d in weekdayChoices"
                  :key="d.value"
                  :value="d.value"
                  size="small"
                  :disabled="sched.weekdays.length === 1 && sched.weekdays[0] === d.value"
                >
                  {{ d.label }}
                </v-btn>
              </v-btn-toggle>
            </div>

            <v-divider class="my-3" />

            <div class="d-flex align-center flex-wrap" style="gap:8px">
              <v-chip
                v-if="cronPreview.valid && cronPreview.next_runs?.length"
                size="small"
                color="success"
                variant="tonal"
                prepend-icon="mdi-clock-check-outline"
              >
                다음 실행 ·
                {{ cronPreview.next_runs.map(formatDateTime).slice(0, 3).join('  ·  ') }}
              </v-chip>
              <v-chip
                v-else-if="sched.type !== 'none' && cronPreview.error"
                size="small"
                color="error"
                variant="tonal"
                prepend-icon="mdi-alert"
              >
                {{ cronPreview.error }}
              </v-chip>
              <v-chip
                v-if="sched.type === 'none'"
                size="small"
                variant="tonal"
                prepend-icon="mdi-hand-back-right-outline"
              >
                수동 실행만 가능 — ▶ 버튼 또는 API 로 직접 실행
              </v-chip>
            </div>
          </v-card>

          <div class="text-overline text-medium-emphasis mb-2 mt-4">인증</div>
          <v-row dense>
            <v-col cols="12" md="4">
              <v-select v-model="form.auth_type" :items="['NONE', 'BASIC', 'API_KEY', 'OAUTH2', 'BEARER']" label="유형" density="comfortable" variant="outlined" />
            </v-col>
            <v-col cols="12" md="8">
              <v-text-field
                v-model="form.auth_secret"
                label="시크릿 (저장 시 AES-GCM 암호화)"
                type="password"
                density="comfortable"
                variant="outlined"
                prepend-inner-icon="mdi-lock-outline"
                :disabled="form.auth_type === 'NONE'"
                hint="기존 값을 바꾸지 않으려면 비워두세요"
                persistent-hint
              />
            </v-col>
          </v-row>

          <!-- 시크릿 변경 사유 — 수정 모드에서 시크릿 입력 시 강제 -->
          <v-text-field
            v-if="form.id && form.auth_secret"
            v-model="form.secret_change_reason"
            label="시크릿 변경 사유 *"
            density="comfortable"
            variant="outlined"
            prepend-inner-icon="mdi-comment-text-outline"
            placeholder="예: KIDI 토큰 만료로 재발급, 보안 정책상 분기별 교체"
            :error="!!form.auth_secret && !form.secret_change_reason"
            hint="개인정보보호법·내부 보안 감사 대응을 위해 사유 기록 필수"
            persistent-hint
            class="mt-2"
          />

          <!-- 인증 키 만료일 — D-7 이내 자동 알림 (auth_type=NONE 이면 비활성) -->
          <v-text-field
            v-model="form.auth_secret_expires_at"
            label="인증 키 만료일 (선택)"
            type="date"
            density="comfortable"
            variant="outlined"
            prepend-inner-icon="mdi-calendar-clock-outline"
            :disabled="form.auth_type === 'NONE'"
            hint="만료 7일 전부터 자동 알림 (3일 이내는 긴급). 비우면 모니터링 안 함."
            persistent-hint
            clearable
            class="mt-2"
          />

          <div class="text-overline text-medium-emphasis mb-2 mt-4">임계값 (선택)</div>
          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="form.response_ms_threshold"
                label="응답시간 임계값 (ms)"
                type="number"
                density="comfortable"
                variant="outlined"
                hint="이 값을 초과하면 SLOW_RESPONSE 장애 자동 감지"
                persistent-hint
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="form.failure_rate_threshold"
                label="실패율 임계값 (0~1)"
                type="number"
                step="0.05"
                density="comfortable"
                variant="outlined"
                hint="최근 15분 윈도우 실패율이 이 값 초과 시 자동 장애"
                persistent-hint
              />
            </v-col>
          </v-row>

          <div class="text-overline text-medium-emphasis mb-2 mt-4">호출 안정성</div>
          <v-card variant="outlined" rounded="lg" class="pa-3 mb-3">
            <div class="text-caption text-medium-emphasis mb-3">
              외부 기관 일시 장애 (5xx / timeout / 네트워크 끊김) 시 자동 재시도. 401·422 같이
              재시도해도 같은 결과인 실패는 즉시 종료. SFTP·MQ·BATCH 는 멱등성 문제로 미적용.
            </div>
            <v-row dense>
              <v-col cols="12" md="4">
                <v-text-field
                  v-model.number="form.timeout_seconds"
                  label="호출 timeout (초)"
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="300"
                  density="comfortable"
                  variant="outlined"
                  placeholder="기본 10초"
                  hint="비우면 시스템 기본값(10s)"
                  persistent-hint
                />
              </v-col>
              <v-col cols="12" md="4">
                <v-text-field
                  v-model.number="form.retry_max"
                  label="최대 재시도 횟수"
                  type="number"
                  min="0"
                  max="5"
                  density="comfortable"
                  variant="outlined"
                  hint="0=재시도 안 함. 권장 2~3"
                  persistent-hint
                />
              </v-col>
              <v-col cols="12" md="4">
                <v-text-field
                  v-model.number="form.retry_backoff_seconds"
                  label="재시도 backoff (초)"
                  type="number"
                  step="0.5"
                  min="0"
                  max="30"
                  density="comfortable"
                  variant="outlined"
                  hint="지수 증가: 1→2→4→8 초"
                  persistent-hint
                />
              </v-col>
            </v-row>
          </v-card>

          <div class="text-overline text-medium-emphasis mb-2 mt-4">알림 채널</div>
          <v-card variant="outlined" rounded="lg" class="pa-3 mb-2">
            <div class="text-caption text-medium-emphasis mb-2">
              장애 발생 시 어느 채널로 알림을 보낼지 선택. 모두 해제해도 incident 자체는 기록됨.
            </div>
            <div class="d-flex" style="gap: 12px; flex-wrap: wrap">
              <v-checkbox
                v-for="ch in alertChannelOptions"
                :key="ch.value"
                :model-value="formChannels.includes(ch.value)"
                :label="ch.label"
                hide-details
                density="compact"
                color="primary"
                @update:model-value="(v) => toggleChannel(ch.value, !!v)"
              />
            </div>
          </v-card>

          <v-divider class="my-4" />
          <div class="d-flex align-start">
            <v-switch
              v-model="form.enabled"
              color="primary"
              label="스케줄 실행 활성"
              hide-details
              density="comfortable"
              class="mt-0"
            />
            <div class="text-caption text-medium-emphasis ml-4 mt-2" style="max-width:430px">
              꺼두면 cron 자동 실행이 멈추지만, 행은 활성 목록에 그대로 보이고
              운영자가 ▶ 수동 실행은 가능합니다.
              <strong>영구히 사용하지 않을 인터페이스는 우상단의 "보관 처리"</strong>
              버튼을 사용하세요 (감사 이력은 영구 보존).
            </div>
          </div>
        </v-card-text>
        <v-divider />
        <v-card-actions class="pa-4">
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">취소</v-btn>
          <v-btn color="primary" variant="elevated" prepend-icon="mdi-content-save-outline" @click="save" :loading="saving">
            저장
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 시크릿 조회 다이얼로그 — 사유 입력 → 평문 1회 노출 -->
    <v-dialog v-model="revealDialog" max-width="540">
      <v-card v-if="revealTarget">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-key-variant" color="warning" class="mr-2" />
          시크릿 조회 — {{ revealTarget.name }}
        </v-card-title>
        <v-card-text>
          <v-alert v-if="!revealedSecret" type="warning" variant="tonal" density="compact" class="mb-3">
            <strong>조회 사유는 감사 로그에 영구 기록됩니다.</strong>
            누가·언제·왜 시크릿을 봤는지 추적 가능해야 보안 감사 통과.
          </v-alert>
          <v-alert v-else type="error" variant="tonal" density="compact" class="mb-3">
            <strong>이 화면을 닫으면 다시 볼 수 없습니다.</strong> 필요한 곳에 즉시
            복사·전달하세요.
          </v-alert>

          <v-text-field
            v-if="!revealedSecret"
            v-model="revealReason"
            label="조회 사유 *"
            placeholder="예: 운영 사고 분석을 위해 KIDI 시크릿 확인"
            density="comfortable"
            variant="outlined"
            prepend-inner-icon="mdi-comment-text-outline"
            autofocus
          />

          <v-text-field
            v-if="revealedSecret"
            :model-value="revealedSecret"
            label="시크릿 (평문)"
            readonly
            density="comfortable"
            variant="outlined"
            append-inner-icon="mdi-content-copy"
            @click:append-inner="copyRevealed"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="closeReveal">{{ revealedSecret ? '확인' : '취소' }}</v-btn>
          <v-btn
            v-if="!revealedSecret"
            color="warning"
            variant="elevated"
            :loading="revealBusy"
            :disabled="!revealReason.trim()"
            @click="confirmReveal"
          >
            조회
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar
      v-model="snack.show"
      :color="snack.color"
      :timeout="snack.action ? 6000 : 2500"
      location="top right"
    >
      {{ snack.text }}
      <template v-if="snack.action" #actions>
        <v-btn variant="text" @click="snack.action.handler(); snack.show = false">
          {{ snack.action.label }}
        </v-btn>
      </template>
    </v-snackbar>
  </div>
</template>

<style scoped>
/* Dim the deleted row's metadata, but keep the action cell crisp so the
   "복원" button stays clearly clickable. */
:deep(tr.row-deleted) {
  background: rgba(120, 120, 120, 0.05);
  box-shadow: inset 3px 0 0 0 rgba(120, 120, 120, 0.5);
}
:deep(tr.row-deleted td:not(:last-child)) {
  opacity: 0.5;
  filter: grayscale(0.4);
}
:deep(tr.row-deleted td:last-child) {
  opacity: 1;
}
/* 토폴로지에서 드릴다운된 row 강조 */
:deep(tr.row-focused) {
  background-color: rgba(33, 150, 243, 0.12);
  outline: 2px solid #2196f3;
}
</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Interfaces, type InterfaceItem } from '@/api/client';
import { cronToLabel, formatDateTime, formatDateTimeShort } from '@/utils/format';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();

type ScheduleType = 'none' | 'minutes' | 'hourly' | 'daily' | 'weekly' | 'monthly';

const scheduleTypes: { value: ScheduleType; label: string }[] = [
  { value: 'none', label: '사용 안 함' },
  { value: 'minutes', label: '매 N분' },
  { value: 'hourly', label: '매시간' },
  { value: 'daily', label: '매일' },
  { value: 'weekly', label: '매주' },
  { value: 'monthly', label: '매월' },
];

const hourChoices = Array.from({ length: 24 }, (_, i) => ({
  value: i,
  title: `${String(i).padStart(2, '0')}시`,
}));
const minuteChoices = [0, 5, 10, 15, 20, 30, 45].map((m) => ({
  value: m,
  title: `${String(m).padStart(2, '0')}분`,
}));
const dayChoices = Array.from({ length: 28 }, (_, i) => ({
  value: i + 1,
  title: `${i + 1}일`,
}));
// APScheduler CronTrigger uses 0=Sun..6=Sat; cron standard same
const weekdayChoices = [
  { value: 1, label: '월' },
  { value: 2, label: '화' },
  { value: 3, label: '수' },
  { value: 4, label: '목' },
  { value: 5, label: '금' },
  { value: 6, label: '토' },
  { value: 0, label: '일' },
];

const protocolOptions = ['REST', 'SOAP', 'FTP', 'MQ', 'BATCH'];

// 통합 관제 분류 (기획서 1번 항목)
const categoryOptions = [
  {
    value: 'INTERNAL_CORE',
    label: '내부 핵심 시스템',
    desc: '사내 보험금계산엔진·CB평가·ESB 등',
    color: 'deep-purple',
    icon: 'mdi-server-network',
  },
  {
    value: 'EXTERNAL_PARTNER',
    label: '외부 제휴사',
    desc: 'PG / 카카오 / 마이데이터 사업자 등',
    color: 'teal',
    icon: 'mdi-handshake-outline',
  },
  {
    value: 'EXTERNAL_REGULATOR',
    label: '외부 규제기관',
    desc: '금감원 / 신용정보원 / 국세청 / 보험개발원 등',
    color: 'red-darken-2',
    icon: 'mdi-bank-outline',
  },
];
const categoryFilterOptions = categoryOptions.map((c) => ({ value: c.value, label: c.label }));

function categoryMeta(c: string | null | undefined) {
  return categoryOptions.find((o) => o.value === c) ?? categoryOptions[1];
}

const filter = reactive<{
  protocol: string | null;
  organization: string | null;
  category: string | null;
  enabledOnly: boolean;
  trashOnly: boolean;
}>({
  protocol: null,
  organization: null,
  category: null,
  enabledOnly: false,
  trashOnly: false,
});

const organizationOptions = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.organization).filter(Boolean))) as string[],
);

const headers = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '방향', key: 'direction', width: 90, sortable: false },
  { title: '분류', key: 'category', width: 130 },
  { title: '이름', key: 'name' },
  { title: '기관', key: 'organization' },
  { title: '프로토콜', key: 'protocol' },
  { title: '엔드포인트', key: 'endpoint' },
  { title: '스케줄', key: 'schedule_cron' },
  { title: '상태', key: 'enabled', width: 160 },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 220 },
];

const rows = ref<InterfaceItem[]>([]);
const loading = ref(false);
const saving = ref(false);
const running = ref<number | null>(null);
const dialog = ref(false);
interface SnackAction { label: string; handler: () => void }
const snack = reactive<{
  show: boolean;
  text: string;
  color: string;
  action: SnackAction | null;
}>({ show: false, text: '', color: 'success', action: null });

// 알림 음소거 / 채널 ----------------------------------------------
const muteOptions = [
  { minutes: 10, label: '10분' },
  { minutes: 30, label: '30분' },
  { minutes: 60, label: '1시간' },
  { minutes: 240, label: '4시간' },
  { minutes: 1440, label: '24시간' },
];
const alertChannelOptions: { value: 'in_app' | 'slack' | 'email'; label: string }[] = [
  { value: 'in_app', label: '🔔 화면 알림 (toast)' },
  { value: 'slack', label: '💬 Slack' },
  { value: 'email', label: '✉️ Email' },
];

function isMuted(item: InterfaceItem): boolean {
  if (!item.muted_until) return false;
  return new Date(item.muted_until).getTime() > Date.now();
}

function muteRemaining(item: InterfaceItem): string {
  if (!item.muted_until) return '';
  const ms = new Date(item.muted_until).getTime() - Date.now();
  if (ms <= 0) return '';
  const totalMin = Math.ceil(ms / 60000);
  if (totalMin < 60) return `${totalMin}분 남음`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m > 0 ? `${h}시간 ${m}분` : `${h}시간`;
}

// 인증 키 만료 임박 상태 — null 이면 모니터링 안 함 / 7일 초과면 표시 안 함.
function expiryStatus(item: InterfaceItem): { color: string; label: string; title: string } | null {
  if (!item.auth_secret_expires_at) return null;
  const now = Date.now();
  const exp = new Date(item.auth_secret_expires_at).getTime();
  const days = Math.floor((exp - now) / 86400_000);
  if (days < 0) {
    return {
      color: 'grey-darken-2',
      label: `만료 ${-days}일 경과`,
      title: `${formatDateTimeShort(item.auth_secret_expires_at)} 에 만료됨 — 즉시 갱신 필요`,
    };
  }
  if (days <= 3) {
    return {
      color: 'error',
      label: days === 0 ? '오늘 만료' : `D-${days}`,
      title: `${formatDateTimeShort(item.auth_secret_expires_at)} 만료 — 긴급 갱신 권장`,
    };
  }
  if (days <= 7) {
    return {
      color: 'warning',
      label: `D-${days}`,
      title: `${formatDateTimeShort(item.auth_secret_expires_at)} 만료 예정 — 곧 갱신 필요`,
    };
  }
  return null;
}

const formChannels = computed<('in_app' | 'slack' | 'email')[]>({
  get: () => (form.alert_channels ?? ['in_app', 'slack', 'email']) as any,
  set: (v) => { form.alert_channels = v; },
});

function toggleChannel(value: 'in_app' | 'slack' | 'email', on: boolean) {
  const cur = new Set<'in_app' | 'slack' | 'email'>(formChannels.value);
  if (on) cur.add(value);
  else cur.delete(value);
  formChannels.value = Array.from(cur);
}

async function mute(item: InterfaceItem, minutes: number) {
  try {
    await Interfaces.mute(item.id, minutes);
    notify(`${item.name} 음소거 — ${minutes < 60 ? minutes + '분' : (minutes / 60) + '시간'}`);
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '음소거 실패', 'error');
  }
}

async function unmute(item: InterfaceItem) {
  try {
    await Interfaces.unmute(item.id);
    notify(`${item.name} 음소거 해제`);
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '해제 실패', 'error');
  }
}

const form = reactive<Partial<InterfaceItem> & { auth_secret?: string; secret_change_reason?: string }>({
  protocol: 'REST',
  method: 'GET',
  auth_type: 'NONE',
  enabled: true,
});

const sched = reactive<{
  type: ScheduleType;
  everyN: number;
  hour: number;
  minute: number;
  weekdays: number[];
  day: number;
}>({
  type: 'none',
  everyN: 10,
  hour: 9,
  minute: 0,
  weekdays: [1, 2, 3, 4, 5],
  day: 1,
});

function buildCron(): string {
  switch (sched.type) {
    case 'none':
      return '';
    case 'minutes':
      return `*/${sched.everyN} * * * *`;
    case 'hourly':
      return `${sched.minute} * * * *`;
    case 'daily':
      return `${sched.minute} ${sched.hour} * * *`;
    case 'weekly': {
      const wds = sched.weekdays.length ? [...sched.weekdays].sort((a, b) => a - b) : [1];
      return `${sched.minute} ${sched.hour} * * ${wds.join(',')}`;
    }
    case 'monthly':
      return `${sched.minute} ${sched.hour} ${sched.day} * *`;
  }
}

function parseCron(expr: string | null | undefined) {
  // Reset to defaults first
  Object.assign(sched, {
    type: 'none' as ScheduleType,
    everyN: 10,
    hour: 9,
    minute: 0,
    weekdays: [1, 2, 3, 4, 5],
    day: 1,
  });
  if (!expr) return;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return; // unsupported custom expression
  const [m, h, d, mo, w] = parts;

  if (mo === '*' && d === '*' && w === '*' && h === '*' && m.startsWith('*/')) {
    sched.type = 'minutes';
    sched.everyN = parseInt(m.slice(2)) || 10;
    return;
  }
  if (mo === '*' && d === '*' && w === '*' && h === '*' && /^\d+$/.test(m)) {
    sched.type = 'hourly';
    sched.minute = parseInt(m);
    return;
  }
  if (mo === '*' && d === '*' && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'daily';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    return;
  }
  if (mo === '*' && d === '*' && w !== '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'weekly';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    const out: number[] = [];
    for (const tok of w.split(',')) {
      if (tok.includes('-')) {
        const [a, b] = tok.split('-').map((n) => parseInt(n));
        for (let i = a; i <= b; i++) out.push(i);
      } else {
        const n = parseInt(tok);
        if (!Number.isNaN(n)) out.push(n);
      }
    }
    sched.weekdays = out.length ? out : [1];
    return;
  }
  if (mo === '*' && /^\d+$/.test(d) && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    sched.type = 'monthly';
    sched.minute = parseInt(m);
    sched.hour = parseInt(h);
    sched.day = parseInt(d);
    return;
  }
  // Unknown pattern — leave as 'none' but preserve raw via form.schedule_cron
}

const cronPreview = reactive<{ valid: boolean; error?: string; next_runs?: string[] }>({
  valid: false,
});
let cronTimer: ReturnType<typeof setTimeout> | null = null;

// Whenever the structured selector changes, rebuild cron → update form
watch(
  sched,
  () => {
    form.schedule_cron = buildCron() || null;
  },
  { deep: true },
);

// Preview on cron change
watch(
  () => form.schedule_cron,
  (expr) => {
    if (cronTimer) clearTimeout(cronTimer);
    if (!expr) {
      Object.assign(cronPreview, { valid: false, error: undefined, next_runs: [] });
      return;
    }
    cronTimer = setTimeout(async () => {
      try {
        const res = await Interfaces.cronPreview(expr.trim());
        Object.assign(cronPreview, res.data);
      } catch {
        Object.assign(cronPreview, { valid: false, error: 'preview 실패' });
      }
    }, 200);
  },
);

function protocolColor(p: string) {
  return { REST: 'primary', SOAP: 'secondary', FTP: 'warning', MQ: 'success', BATCH: 'purple' }[p] ?? 'grey';
}

function notify(text: string, color = 'success', action: SnackAction | null = null) {
  Object.assign(snack, { show: true, text, color, action });
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, unknown> = {};
    if (filter.protocol) params.protocol = filter.protocol;
    if (filter.organization) params.organization = filter.organization;
    if (filter.category) params.category = filter.category;
    if (filter.trashOnly) {
      params.only_deleted = true;
    } else if (filter.enabledOnly) {
      params.enabled = true;
    }
    rows.value = (await Interfaces.list(params)).data;
  } finally {
    loading.value = false;
  }
}

function rowProps({ item }: { item: InterfaceItem }) {
  const classes: string[] = [];
  if (item.deleted_at) classes.push('row-deleted');
  if (focusedId.value === item.id) classes.push('row-focused');
  return classes.length ? { class: classes.join(' ') } : {};
}

function openCreate() {
  Object.assign(form, {
    id: undefined,
    name: '',
    organization: '',
    category: 'EXTERNAL_PARTNER',
    direction: 'OUTBOUND',
    protocol: 'REST',
    method: 'GET',
    endpoint: '',
    schedule_cron: '',
    auth_type: 'NONE',
    auth_secret: '',
    secret_change_reason: '',
    response_ms_threshold: null,
    failure_rate_threshold: null,
    timeout_seconds: null,
    retry_max: 0,
    retry_backoff_seconds: 1.0,
    auth_secret_expires_at: null,
    enabled: true,
  });
  parseCron('');
  dialog.value = true;
}

function openEdit(item: InterfaceItem) {
  // <input type="date"> 는 YYYY-MM-DD 만 받음 — ISO 8601 datetime 의 앞 10자만 잘라 매핑.
  const expiresDate = item.auth_secret_expires_at
    ? item.auth_secret_expires_at.slice(0, 10)
    : null;
  Object.assign(form, item, {
    auth_secret: '',
    secret_change_reason: '',
    auth_secret_expires_at: expiresDate,
  });
  parseCron(item.schedule_cron ?? '');
  dialog.value = true;
}

async function save() {
  // 클라이언트 사전 검증 — 시크릿 변경 시 사유 필수
  if (form.id && form.auth_secret && !form.secret_change_reason?.trim()) {
    notify('시크릿을 변경하려면 변경 사유를 입력해주세요.', 'error');
    return;
  }
  saving.value = true;
  try {
    const payload = { ...form };
    if (!payload.auth_secret) {
      delete payload.auth_secret;
      delete payload.secret_change_reason;
    }
    if (form.id) await Interfaces.update(form.id, payload);
    else {
      delete payload.secret_change_reason;
      await Interfaces.create(payload);
    }
    notify('저장 완료');
    dialog.value = false;
    await load();
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '저장 실패', 'error');
  } finally {
    saving.value = false;
  }
}

async function remove(item: InterfaceItem) {
  const msg =
    `'${item.name}' 을(를) 보관 처리할까요?\n\n` +
    `· 호출 로그·장애 이력·SLA 목표는 감사 대응을 위해 영구 보존됩니다.\n` +
    `· 스케줄 실행은 즉시 중지됩니다.\n` +
    `· 휴지통에서 언제든 복원할 수 있습니다.\n\n` +
    `※ 잠시만 멈출 거면 행을 클릭해 "스케줄 실행" 토글을 끄는 것이 적절합니다.`;
  if (!confirm(msg)) return;
  await Interfaces.remove(item.id);
  notify('보관 처리됨 — 휴지통에서 복원 가능');
  await load();
}

async function archiveFromDialog() {
  if (!form.id) return;
  const target = rows.value.find((r) => r.id === form.id);
  if (!target) return;
  await remove(target);
  dialog.value = false;
}

async function restore(item: InterfaceItem) {
  await Interfaces.restore(item.id);
  notify(`${item.name} 복원됨`);
  await load();
}

// --- 시크릿 조회 ----------------------------------------------------------
const revealDialog = ref(false);
const revealTarget = ref<InterfaceItem | null>(null);
const revealReason = ref('');
const revealedSecret = ref('');
const revealBusy = ref(false);

function openReveal(item: InterfaceItem) {
  revealTarget.value = item;
  revealReason.value = '';
  revealedSecret.value = '';
  revealDialog.value = true;
}

async function confirmReveal() {
  if (!revealTarget.value || !revealReason.value.trim()) return;
  revealBusy.value = true;
  try {
    const res = await Interfaces.revealSecret(revealTarget.value.id, revealReason.value.trim());
    revealedSecret.value = res.data.secret;
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '조회 실패', 'error');
  } finally {
    revealBusy.value = false;
  }
}

async function copyRevealed() {
  if (!revealedSecret.value) return;
  try {
    await navigator.clipboard.writeText(revealedSecret.value);
    notify('클립보드에 복사됨');
  } catch {
    notify('복사 실패', 'error');
  }
}

function closeReveal() {
  revealDialog.value = false;
  // 평문은 메모리에서도 즉시 제거 (브라우저 dev tools 노출 최소화)
  setTimeout(() => {
    revealedSecret.value = '';
    revealReason.value = '';
    revealTarget.value = null;
  }, 200);
}

async function run(item: InterfaceItem) {
  running.value = item.id;
  try {
    const res = await Interfaces.execute(item.id);
    const status = res.data.status;
    const text = `${item.name} → ${status} (${res.data.duration_ms}ms)`;
    if (status === 'SUCCESS') {
      notify(text, 'success');
    } else {
      // 실패 시 — 페이지 내 알림 + 장애 페이지로 바로 이동 액션. 같은 유형의
      // 미해결 incident 가 있으면 별도 toast 는 안 뜨므로 (alarm fatigue 방지),
      // 여기서 명시적으로 장애 페이지 안내해야 사용자가 흐름 놓치지 않음.
      notify(text, 'error', {
        label: '장애 보기',
        handler: () => router.push({
          path: '/incidents',
          query: { unresolved: '1' },
        }),
      });
    }
  } catch (e: any) {
    notify(e?.response?.data?.detail ?? '실행 실패', 'error');
  } finally {
    running.value = null;
  }
}

// 토폴로지에서 ?focus=ID 로 진입하면 해당 row 강조
const route = useRoute();
const router = useRouter();
const focusedId = ref<number | null>(null);
// focus 진입 시 필터 (분류/프로토콜/기관/활성만/휴지통) 를 모두 해제 — 어떤 상태의
// 인터페이스든 (휴지통 포함) 항상 강조 표시될 수 있도록.
function clearFiltersForFocus() {
  filter.category = null;
  filter.protocol = null;
  filter.organization = null;
  filter.enabledOnly = false;
  filter.trashOnly = false;
}

const focusedRowExists = computed(
  () => focusedId.value != null && rows.value.some((r) => r.id === focusedId.value),
);

onMounted(async () => {
  const fp = Number(route.query.focus);
  if (Number.isFinite(fp) && fp > 0) {
    focusedId.value = fp;
    clearFiltersForFocus();
  }
  await load();
});
watch(
  () => route.query.focus,
  async (v) => {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) {
      if (focusedId.value !== n) {
        focusedId.value = n;
        clearFiltersForFocus();
        await load();
      }
      return;
    }
    // focus 사라짐 → 강조 해제 (필터는 사용자가 다시 설정하지 않음 — 의도 보존)
    focusedId.value = null;
  },
);
</script>
