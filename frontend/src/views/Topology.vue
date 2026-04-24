<template>
  <div>
    <div class="d-flex align-center mb-3">
      <h2 class="text-h5">통합관제 토폴로지</h2>
      <v-chip class="ml-3" size="small" color="primary" variant="tonal" prepend-icon="mdi-graph-outline">
        의존성 지도
      </v-chip>
      <v-spacer />
      <v-btn-toggle v-model="windowDays" mandatory density="compact" variant="outlined" color="primary">
        <v-btn :value="1" size="small">1일</v-btn>
        <v-btn :value="7" size="small">7일</v-btn>
        <v-btn :value="30" size="small">30일</v-btn>
      </v-btn-toggle>
      <v-btn icon="mdi-refresh" size="small" variant="text" class="ml-2" @click="loadAll" />
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <strong>사내 핵심 시스템 ↔ 외부 기관</strong> 인터페이스 의존성 지도. 카드 클릭 시 상세 이동.
    </v-alert>

    <v-row v-if="loaded">
      <!-- 왼쪽: 사내 핵심 시스템 -->
      <v-col cols="12" md="3">
        <v-card height="100%" variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-server-network" color="deep-purple" class="mr-2" />
            사내 핵심 시스템
            <v-chip size="x-small" class="ml-2" color="deep-purple" variant="flat">
              {{ groups.INTERNAL_CORE.length }}
            </v-chip>
            <v-spacer />
            <v-btn
              v-if="auth.isAdmin"
              icon="mdi-plus"
              size="x-small"
              variant="tonal"
              color="deep-purple"
              title="사내 핵심 시스템 인터페이스 추가"
              @click="addInterface('INTERNAL_CORE')"
            />
          </v-card-title>
          <v-card-text>
            <div v-if="!groups.INTERNAL_CORE.length" class="text-caption text-medium-emphasis">
              등록된 사내 시스템 없음
            </div>
            <v-card
              v-for="itf in groups.INTERNAL_CORE"
              :key="itf.id"
              class="mb-2"
              variant="tonal"
              :color="healthColor(itf.id)"
              link
              @click="goToInterface(itf.id)"
            >
              <v-card-text class="py-2 px-3">
                <div class="d-flex align-center">
                  <v-icon :icon="directionIcon(itf.direction)" size="small" class="mr-2" />
                  <div style="flex: 1; min-width: 0">
                    <div class="text-body-2 font-weight-medium text-truncate">{{ itf.name }}</div>
                    <div class="text-caption text-medium-emphasis text-truncate">
                      {{ itf.organization || '-' }} · {{ itf.protocol }}
                    </div>
                  </div>
                  <v-chip size="x-small" :color="healthChipColor(itf.id)" variant="flat">
                    {{ healthLabel(itf.id) }}
                  </v-chip>
                </div>
              </v-card-text>
            </v-card>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- 화살표 영역 -->
      <v-col
        cols="12"
        md="1"
        class="d-none d-md-flex flex-column align-center justify-center"
        style="padding: 0"
      >
        <v-icon icon="mdi-arrow-right-bold" size="40" color="grey-darken-1" />
        <div class="text-caption text-medium-emphasis text-center mt-1" style="line-height: 1.2">
          OUTBOUND<br />호출
        </div>
        <v-icon icon="mdi-arrow-left-bold" size="40" color="deep-purple-lighten-1" class="mt-3" />
        <div class="text-caption text-medium-emphasis text-center mt-1" style="line-height: 1.2">
          INBOUND<br />webhook
        </div>
      </v-col>

      <!-- 오른쪽: 외부 기관 -->
      <v-col cols="12" md="8">
        <v-row dense>
          <v-col cols="12" md="6">
            <v-card height="100%" variant="outlined">
              <v-card-title class="d-flex align-center">
                <v-icon icon="mdi-handshake-outline" color="teal" class="mr-2" />
                외부 제휴사
                <v-chip size="x-small" class="ml-2" color="teal" variant="flat">
                  {{ groups.EXTERNAL_PARTNER.length }}
                </v-chip>
                <v-spacer />
                <v-btn
                  v-if="auth.isAdmin"
                  icon="mdi-plus"
                  size="x-small"
                  variant="tonal"
                  color="teal"
                  title="외부 제휴사 인터페이스 추가"
                  @click="addInterface('EXTERNAL_PARTNER')"
                />
              </v-card-title>
              <v-card-text>
                <div v-for="(itfs, org) in partnerByOrg" :key="org" class="mb-3">
                  <div class="text-overline text-medium-emphasis mb-1">{{ org }}</div>
                  <v-card
                    v-for="itf in itfs"
                    :key="itf.id"
                    variant="tonal"
                    :color="healthColor(itf.id)"
                    class="mb-1"
                    link
                    @click="goToInterface(itf.id)"
                  >
                    <v-card-text class="py-2 px-3">
                      <div class="d-flex align-center">
                        <v-icon :icon="directionIcon(itf.direction)" size="small" class="mr-2" />
                        <div style="flex: 1; min-width: 0">
                          <div class="text-body-2 text-truncate">{{ itf.name }}</div>
                        </div>
                        <v-chip size="x-small" variant="outlined" class="mr-1">{{ itf.protocol }}</v-chip>
                        <v-chip size="x-small" :color="healthChipColor(itf.id)" variant="flat">
                          {{ healthLabel(itf.id) }}
                        </v-chip>
                      </div>
                    </v-card-text>
                  </v-card>
                </div>
                <div v-if="!groups.EXTERNAL_PARTNER.length" class="text-caption text-medium-emphasis">
                  등록된 제휴사 없음
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="6">
            <v-card height="100%" variant="outlined">
              <v-card-title class="d-flex align-center">
                <v-icon icon="mdi-bank-outline" color="red-darken-2" class="mr-2" />
                외부 규제기관
                <v-chip size="x-small" class="ml-2" color="red-darken-2" variant="flat">
                  {{ groups.EXTERNAL_REGULATOR.length }}
                </v-chip>
                <v-spacer />
                <v-btn
                  v-if="auth.isAdmin"
                  icon="mdi-plus"
                  size="x-small"
                  variant="tonal"
                  color="red-darken-2"
                  title="외부 규제기관 인터페이스 추가"
                  @click="addInterface('EXTERNAL_REGULATOR')"
                />
              </v-card-title>
              <v-card-text>
                <div v-for="(itfs, org) in regulatorByOrg" :key="org" class="mb-3">
                  <div class="text-overline text-medium-emphasis mb-1">{{ org }}</div>
                  <v-card
                    v-for="itf in itfs"
                    :key="itf.id"
                    variant="tonal"
                    :color="healthColor(itf.id)"
                    class="mb-1"
                    link
                    @click="goToInterface(itf.id)"
                  >
                    <v-card-text class="py-2 px-3">
                      <div class="d-flex align-center">
                        <v-icon :icon="directionIcon(itf.direction)" size="small" class="mr-2" />
                        <div style="flex: 1; min-width: 0">
                          <div class="text-body-2 text-truncate">{{ itf.name }}</div>
                        </div>
                        <v-chip size="x-small" variant="outlined" class="mr-1">{{ itf.protocol }}</v-chip>
                        <v-chip size="x-small" :color="healthChipColor(itf.id)" variant="flat">
                          {{ healthLabel(itf.id) }}
                        </v-chip>
                      </div>
                    </v-card-text>
                  </v-card>
                </div>
                <div v-if="!groups.EXTERNAL_REGULATOR.length" class="text-caption text-medium-emphasis">
                  등록된 규제기관 없음
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </v-col>
    </v-row>

    <!-- 헬스 요약 -->
    <v-card v-if="loaded" class="mt-4">
      <v-card-text class="d-flex align-center" style="gap: 24px; flex-wrap: wrap">
        <div>
          <div class="text-overline text-medium-emphasis">전체 인터페이스</div>
          <div class="text-h5">{{ allInterfaces.length }}</div>
        </div>
        <v-divider vertical />
        <div>
          <div class="text-overline text-medium-emphasis">방향</div>
          <v-chip color="primary" variant="tonal" prepend-icon="mdi-arrow-right-bold-outline">
            OUTBOUND {{ outboundCount }}
          </v-chip>
          <v-chip color="deep-purple" variant="tonal" prepend-icon="mdi-arrow-left-bold-outline" class="ml-1">
            INBOUND {{ inboundCount }}
          </v-chip>
        </div>
        <v-divider vertical />
        <div>
          <div class="text-overline text-medium-emphasis">최근 {{ windowDays }}일 헬스</div>
          <v-chip color="success" variant="tonal" prepend-icon="mdi-check-circle">
            정상 {{ healthCounts.healthy }}
          </v-chip>
          <v-chip color="warning" variant="tonal" prepend-icon="mdi-alert" class="ml-1">
            주의 {{ healthCounts.degraded }}
          </v-chip>
          <v-chip color="error" variant="tonal" prepend-icon="mdi-fire" class="ml-1">
            장애 {{ healthCounts.down }}
          </v-chip>
          <v-chip color="grey" variant="tonal" prepend-icon="mdi-help-circle-outline" class="ml-1">
            데이터 없음 {{ healthCounts.unknown }}
          </v-chip>
        </div>
      </v-card-text>
    </v-card>

    <v-progress-linear v-if="loading" indeterminate color="primary" class="mt-4" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Interfaces, Performance, type InterfaceItem } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const auth = useAuthStore();

// 분류별 인터페이스 추가 — Interfaces 페이지로 이동하면서 ?create=CATEGORY 신호.
// Interfaces 페이지가 이를 받아 등록 다이얼로그 자동 오픈 + 해당 분류 미리 선택.
function addInterface(category: 'INTERNAL_CORE' | 'EXTERNAL_PARTNER' | 'EXTERNAL_REGULATOR') {
  router.push({ path: '/interfaces', query: { create: category } });
}

const allInterfaces = ref<InterfaceItem[]>([]);
const statsByInterface = ref<Map<number, { total: number; failures: number }>>(new Map());
const loading = ref(false);
const loaded = ref(false);
const windowDays = ref(7);

async function loadAll() {
  loading.value = true;
  try {
    // 인터페이스 목록 + 인터페이스별 집계 통계 (percentiles 가 이미 GROUP BY interface_id 결과)
    const [itfRes, perfRes] = await Promise.all([
      Interfaces.list({ include_deleted: false }),
      Performance.percentiles(windowDays.value),
    ]);
    allInterfaces.value = itfRes.data;
    const map = new Map<number, { total: number; failures: number }>();
    for (const r of perfRes.data) {
      map.set(r.interface_id, { total: r.total_calls, failures: r.failure_count });
    }
    statsByInterface.value = map;
    loaded.value = true;
  } finally {
    loading.value = false;
  }
}

watch(windowDays, loadAll);
onMounted(loadAll);

const groups = computed(() => ({
  INTERNAL_CORE: allInterfaces.value.filter((i) => i.category === 'INTERNAL_CORE'),
  EXTERNAL_PARTNER: allInterfaces.value.filter((i) => i.category === 'EXTERNAL_PARTNER'),
  EXTERNAL_REGULATOR: allInterfaces.value.filter((i) => i.category === 'EXTERNAL_REGULATOR'),
}));

function groupByOrg(arr: InterfaceItem[]): Record<string, InterfaceItem[]> {
  const map: Record<string, InterfaceItem[]> = {};
  for (const itf of arr) {
    const key = itf.organization || '(미지정)';
    if (!map[key]) map[key] = [];
    map[key].push(itf);
  }
  return map;
}

const partnerByOrg = computed(() => groupByOrg(groups.value.EXTERNAL_PARTNER));
const regulatorByOrg = computed(() => groupByOrg(groups.value.EXTERNAL_REGULATOR));

const outboundCount = computed(
  () => allInterfaces.value.filter((i) => i.direction !== 'INBOUND').length,
);
const inboundCount = computed(
  () => allInterfaces.value.filter((i) => i.direction === 'INBOUND').length,
);

function failureRate(id: number): number | null {
  const s = statsByInterface.value.get(id);
  if (!s || s.total === 0) return null;
  return s.failures / s.total;
}

// healthColor: v-card variant=tonal 의 background tint
function healthColor(id: number): string {
  const r = failureRate(id);
  if (r === null) return 'surface';
  if (r >= 0.1) return 'error';
  if (r >= 0.03) return 'warning';
  return 'success';
}
function healthChipColor(id: number): string {
  const r = failureRate(id);
  if (r === null) return 'grey';
  if (r >= 0.1) return 'error';
  if (r >= 0.03) return 'warning';
  return 'success';
}
function healthLabel(id: number): string {
  const r = failureRate(id);
  if (r === null) return '데이터 없음';
  return `${(r * 100).toFixed(1)}%`;
}

const healthCounts = computed(() => {
  let healthy = 0, degraded = 0, down = 0, unknown = 0;
  for (const itf of allInterfaces.value) {
    const r = failureRate(itf.id);
    if (r === null) unknown += 1;
    else if (r >= 0.1) down += 1;
    else if (r >= 0.03) degraded += 1;
    else healthy += 1;
  }
  return { healthy, degraded, down, unknown };
});

function directionIcon(d: string | undefined): string {
  return d === 'INBOUND' ? 'mdi-arrow-left-bold-outline' : 'mdi-arrow-right-bold-outline';
}

function goToInterface(id: number) {
  router.push({ path: '/interfaces', query: { focus: String(id) } });
}
</script>
