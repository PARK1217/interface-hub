# 기획서 업데이트 가이드

> 목적: 기존 17 슬라이드 PPT (`Interface_Hub_기획서.pptx`) 의
> **디자인·레이아웃·슬라이드 수는 그대로 유지**하고 텍스트만 실제 구현에 맞게 보강.
> 새 슬라이드 추가는 하지 마세요 — 기존 칸 안에 녹여 표현.

---

## 전체 변경 요약

| 영역 | 기획 → 실제 |
|---|---|
| **AI 차별화** | LangChain · FAISS · OpenAI Embedding → **TF-IDF 검색 + 멀티 LLM 프로바이더 (Mistral/Anthropic/OpenAI/HuggingFace) + fallback 체인** |
| **DB 테이블** | 5종 (interfaces / call_logs / incidents / sla_targets / vector_cases) → **8종** (vector_cases 미사용, **users / audit_logs / alert_rules / ai_query_logs** 추가) |
| **핵심 기능 4종** | 큰 틀 유지 + 각 기능 안에 운영 디테일 보강 (재시도·방향·분류·알림룰·키만료·자동복구 등) |
| **운영 보안** | 기획에 없던 **JWT 인증 + 5회 잠금 + 강제 비번 변경 + 감사 로그** 추가 |

---

## 슬라이드별 수정 지시

### Slide 5 — DIFFERENTIATOR (AI 기반 차별화)

**기존 우측 박스**: "LLM + RAG · 원인 분석 / LangChain · FAISS · OpenAI API"

**교체**:
- 박스 제목: "LLM + RAG · 원인 분석"
- 본문 3줄:
  - 과거 장애 이력을 TF-IDF 로 색인해 신규 질문과 cosine 유사도 검색
  - 신규 장애에 유사도 Top-K 사례 자동 제시
  - 멀티 LLM (Mistral / Anthropic / OpenAI / HuggingFace) 자동 fallback — 한 곳 장애 시 다른 곳으로
- 하단 라이브러리 라벨: `scikit-learn (TF-IDF) · httpx · 멀티 프로바이더`

**추가 차별점 (텍스트 한 줄 추가 가능)**:
- 질문 의도 자동 분류 (운영 통계 / 사례 검색 / 설정 조회) → 환각 방지

### Slide 9 — DIFFERENTIATOR 상세 (RAG)

**기존 좌측 박스 ML/이상 탐지** — 그대로 유지 (Isolation Forest 코드는 services/ai/anomaly.py 에 있음)

**기존 우측 박스 (LLM + RAG)**:
- "장애 이력·조치 로그를 임베딩해 FAISS VectorDB 에 저장" → **"장애 이력을 TF-IDF char-ngram 으로 색인"**
- "신규 장애 발생 시 유사도 Top-N 사례를 자동 제시" → 그대로
- "LangChain 체인으로 ..." → **"멀티 LLM 호출 + 자동 fallback 체인 — 한 프로바이더 실패 시 다음 프로바이더로 자동 전환"**
- 하단 라이브러리: `LangChain · FAISS · OpenAI API` → **`scikit-learn · httpx · Mistral/Anthropic/OpenAI/HuggingFace`**

### Slide 10 — RAG 파이프라인

**5단계 박스 텍스트 교체**:
- ① 장애 이력 수집 — 그대로
- ② **임베딩 → "TF-IDF 벡터화 (char n-gram 2-4)"**
- ③ **FAISS 저장 → "메모리 색인 (요청 시 fit_transform)"**
- ④ 유사 사례 검색 — 그대로 (Top-K cosine)
- ⑤ **LLM 요약 → "멀티 LLM 호출 → 자동 fallback → markdown 응답"**

**EXAMPLE 박스 마지막 줄 추가 가능**:
- "운영자가 답변의 incident #42 링크를 클릭하면 즉시 장애 상세로 드릴다운"

### Slide 6 — 핵심 기능 4종 Overview

**각 카드에 한 줄 추가**:
- **F1 인터페이스 등록·실행**:
  - 추가 줄: "사내·외부 분류 + 호출 방향(OUT/IN) + 자동 재시도/backoff"
- **F2 실시간 모니터링**:
  - 추가 줄: "사내↔외부 의존성 토폴로지 뷰"
- **F3 장애 감지·알림**:
  - 추가 줄: "전역 알림 룰 + 인터페이스별 음소거 + 인증키 만료 임박 자동 알림"
- **F4 로그·SLA 분석**:
  - 추가 줄: "재시도 자동 복구 분석 + 모든 변경 감사 로그"

### Slide 7 — F1·F2 상세

**F1 인터페이스 등록·실행** (3 sub-box):
- "통합 등록 폼" — 그대로 + "**분류(내부 핵심/외부 제휴/외부 규제기관) · 호출 방향(OUTBOUND/INBOUND)**" 명시
- "스케줄 · 수동 실행" — 그대로 + "**자동 재시도 정책 (timeout / 횟수 / backoff) — REST/SOAP 한정**"
- "인증정보 안전 저장" — 그대로 + "**+ 인증 키 만료일 추적, D-7 임박 시 자동 알림**"

**F2 실시간 모니터링** (3 sub-box):
- "실시간 대시보드" — 그대로
- "WebSocket 라이브 스트림" — 그대로
- "히트맵 뷰" — 그대로 + "**+ 사내↔외부 통합 토폴로지 지도 (의존성 시각화)**"

### Slide 8 — F3·F4 상세

**F3 장애 감지·자동 알림** (3 sub-box):
- "임계값 기반 자동 감지" — 그대로
- "오류 유형 자동 분류" — 그대로
- "멀티채널 알림" — 그대로 + "**+ 전역 알림 룰 (severity 라우팅, 야간/주말 silence) + 인터페이스별 음소거**"

**F4 로그 · SLA 분석** (3 sub-box):
- "중앙 로그 수집" — 그대로 + "**+ 모든 변경 액션 감사 로그 (append-only, 금감원 보고 대응)**"
- "고급 검색" — 그대로 + "**시간 구간 필터 + 빠른 선택 (1h/6h/24h/7d)**"
- "SLA 달성률 리포트" — 그대로 + "**+ 자동 복구 분석 페이지 (재시도 정책 효과 KPI)**"

### Slide 11 — 시스템 아키텍처

**API Layer (FastAPI)** 박스에 한 줄 추가:
- "JWT 인증 · 감사 로그 미들웨어"

**AI Layer (Python)** 박스 내용 교체:
- "scikit-learn 이상 탐지" — 그대로
- "**LangChain + FAISS RAG**" → "**TF-IDF 검색 + 멀티 LLM (httpx) + fallback 체인**"

### Slide 12 — 기술 스택 & 바이브코딩

**기술 스택 박스** (좌측):
- Frontend: Vue 3 · Vite · Vuetify 3 · ApexCharts → 그대로 + "**marked + DOMPurify (AI 답변 markdown 렌더)**"
- Backend: FastAPI · SQLAlchemy · Pydantic → 그대로 + "**APScheduler · paramiko (SFTP) · croniter**"
- AI / ML: scikit-learn · LangChain · FAISS → **"scikit-learn (TF-IDF) · httpx (멀티 LLM 직접 호출)"**
- Database: PostgreSQL 15 · Redis 7 → 그대로 (Redis 는 캐시 + MQ 시뮬)
- Realtime: WebSocket (Starlette) → 그대로
- DevOps: Docker · GitHub Actions · Jenkins 호환 → 그대로

### Slide 13 — 데이터 흐름 시퀀스

**참여자 (좌→우)**: 운영자 / FastAPI Server / 외부 기관 API / AI Layer / Notifier — 그대로

**시퀀스 단계** — 그대로 유지하되 ④ 옆에 한 줄 부연:
- "④ 이상 탐지 + RAG 질의" → "④ 이상 탐지 + RAG (TF-IDF Top-K) + 멀티 LLM 자동 fallback"

### Slide 14 — DB 설계 (핵심 테이블) — **가장 큰 수정**

**기존 5종 카드** → **카드 8종 + 1종 미사용** (자리가 좁으면 카드 크기 축소)

| # | 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|---|
| 1 | **interfaces** | id PK, name, **category**, **direction**, protocol, endpoint, schedule_cron, auth_type, **timeout_seconds**, **retry_max**, **retry_backoff_seconds**, **muted_until**, **alert_channels**, **auth_secret_expires_at** | 기획서 컬럼 + **B.7/B.9/B.12/B.13** 추가 컬럼 |
| 2 | **call_logs** | id PK, interface_id FK, request, response, status, duration_ms, called_at, **attempt_count**, **parent_log_id**, **retry_count**, **is_reprocessed**, **actor_user_id** | + 자동 재시도/재처리 추적 |
| 3 | **incidents** | id PK, interface_id FK, type, severity, summary, detected_at, resolved_at, root_cause, resolution | + **type 에 SECRET_EXPIRY_WARNING 추가** |
| 4 | **sla_targets** | id PK, interface_id FK, uptime_target, response_ms_target | 그대로 |
| 5 | **users** ⭐신규 | id PK, username, password_hash, role, **failed_login_count**, **locked_until**, **must_change_password**, **session_version** | 계정 보안 + 강제 로그아웃 |
| 6 | **audit_logs** ⭐신규 | id PK, actor_user_id FK, action, resource_type, resource_id, before_value, after_value, ip, user_agent, occurred_at | 모든 변경 추적 (append-only) |
| 7 | **alert_rules** ⭐신규 | id (단일 행), info_channels, warning_channels, critical_channels, quiet_hours_enabled, quiet_hours_start/end, weekend_silence | 전역 알림 정책 |
| 8 | **ai_query_logs** ⭐신규 | id PK, actor_user_id FK, question, question_hash, response_excerpt, mode, provider, hit_cache, llm_error_kind, outcome, asked_at | AI 분석 이력·캐싱·통계 |
| -  | ~~vector_cases~~ | ~~FAISS 색인용~~ | **TF-IDF 로 대체되어 미사용** (DB 자리만, 또는 슬라이드에서 제거) |

**자리 부족하면**:
- vector_cases 카드 삭제
- 8종을 2x4 grid 로 재배치
- 또는 컬럼은 핵심 3-4개만 표시 (전체는 "외 N개")

### Slide 15 — 개발 로드맵 Phase 1~3

**Phase 3 AI BOOST** 카드 항목 교체:
- "ML 이상 탐지 모델" — 그대로
- "**장애 이력 임베딩 · FAISS 색인**" → "**장애 이력 TF-IDF 색인 + 의도 분류**"
- "**LangChain 원인 분석 체인**" → "**멀티 LLM (Mistral/Anthropic/OpenAI/HuggingFace) + fallback 체인**"
- "운영자 질의 챗봇 UI" — 그대로 + "**+ markdown 답변 + incident 드릴다운 + 진행 메시지**"

**Phase 2 CORE** 카드에 한 줄 추가 가능:
- "전역 알림 룰 + 음소거 + 인증 키 만료 모니터링"

### Slide 16 — 기대 효과

**상단 3 KPI 박스** — 그대로 유지

**정성적 기대 효과 영역** — 다음 줄 추가 가능:
- 운영 보안 강화 — JWT 인증, 5회 실패 자동 잠금, 모든 변경 감사 추적으로 금감원 보고 대응
- 외부 일시 장애 자동 흡수 — 자동 재시도/backoff 로 SLA 개선 효과 정량화 (자동 복구 분석 페이지)

---

## 절대 건드리지 말 것
- 표지 (Slide 1) 의 본인 정보·로고
- 목차 (Slide 2)
- 클로징 (Slide 17)
- 색상·폰트·전체 레이아웃·아이콘
- 페이지 번호·푸터

## 디자인 가이드
- 새로 추가하는 텍스트도 기존 폰트·색·크기 유지
- 추가 항목은 기존 bullet 스타일 그대로 (•, -, ▶ 등)
- 한국어 본문은 기존 줄바꿈·여백 흐름 따름
