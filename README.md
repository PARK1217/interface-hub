# Interface Hub

> 보험사 외부 인터페이스를 한 곳에서 **등록 · 실행 · 감시 · 재처리 · 분석**하는 통합 관제 플랫폼.

흩어진 REST/SOAP/MQ/Batch/SFTP 연동을 하나의 UI 로 모으고, 실시간 모니터링·자동 장애 감지·운영자 재처리·AI(RAG) 기반 원인 분석까지 한 화면에서 처리합니다.

---

## 핵심 기능

| 구분 | 기능 | 상태 |
|---|---|---|
| F1 | 인터페이스 등록·실행 (REST / SOAP / MQ / Batch / SFTP, Cron, AES-GCM 시크릿) | ✅ |
| F2 | 실시간 모니터링 (WebSocket 라이브 + ApexCharts 시계열·히트맵) | ✅ |
| F3 | 장애 자동 감지·분류 (Timeout/Auth/Format/5xx) + Slack·Email + in-app toast/뱃지 + **인터페이스별 음소거·채널 토글** | ✅ |
| F4 | 로그 검색 + 재처리 (단건 ↻ / 일괄 / lineage 체인) + 상세 다이얼로그 | ✅ |
| F5 | 성능 관리 (p50/p95/p99 백분위, TPS, Slow Top 10) | ✅ |
| F6 | SLA 리포트 (가동률 / 응답시간 vs 목표) | ✅ |
| F7 | AI 분석 — RAG (LangChain + FAISS) 또는 Fallback (TF-IDF) 자동 분기 | ✅ |
| F8 | Ingest API — 외부 시스템이 자기 호출 결과를 보고하는 통로 | ✅ |

## 5종 프로토콜 어댑터

| 프로토콜 | 동작 | 비고 |
|---|---|---|
| **REST** | ✅ 실호출 (httpx) | 4xx/5xx 자동 분류 |
| **SOAP** | ✅ 실호출 (httpx + XML body) | WSDL 파싱은 추후 |
| **SFTP/FTP** | ✅ 실호출 (paramiko, atmoz/sftp 컨테이너) | LIST · GET · PUT 3종 op |
| **Batch** | ✅ 시뮬 (파일 픽업 → 처리 → 결과 업로드) | records/errors/duration 반환 |
| **MQ** | ✅ Redis LIST 시뮬 (BLPOP) | 시드에서 40개 메시지 prepopulate |

## 아키텍처

```
┌──────────────┐  HTTP/WS    ┌──────────────────────┐  REST/SOAP   ┌──────────────┐
│ Vue 3 + Vite │ ◀─────────▶ │ FastAPI (Python 3.12) │ ◀─────────▶ │ 외부 기관 API │
│ Vuetify 3    │             │  · CRUD / Scheduler   │  SFTP/MQ    │ KIDI·KCIS·   │
│ ApexCharts   │             │  · WebSocket Live     │             │ 심평원·KFTC  │
└──────────────┘             │  · Detector·Notifier  │             └──────────────┘
                             │  · RAG (LLM/Fallback) │
                             └─────────┬─────────────┘
                                       │
                          ┌────────────┴────────────┬─────────────┐
                          │                         │             │
                  PostgreSQL 15               Redis 7        atmoz/sftp
                  (5 핵심 테이블)               (MQ 큐)        (SFTP 서버)
                                                              FAISS index (디스크)
```

5개 핵심 테이블: `interfaces` · `call_logs` · `incidents` · `sla_targets` · `vector_cases`.

## 빠른 시작 (Docker)

```bash
# 1) 환경 변수 준비
cp backend/.env.example backend/.env
# (선택) AI 사용 시 OPENAI_API_KEY, Slack/SMTP 값 채우기 — 안 채워도 fallback 모드로 동작

# 2) 전체 스택 기동 (postgres / redis / sftp / backend / frontend)
docker compose up --build

# 3) 시드 데이터 적재 — 처음 한 번
docker compose exec backend python -m app.scripts.seed_demo
# → 14 인터페이스 + ~12k 호출로그 + 장애 6건 + Redis 큐 40개 메시지

# 4) 접속
#   · Frontend  http://localhost:5173
#   · Backend   http://localhost:8000/docs   (Swagger)
#   · Health    http://localhost:8000/health
```

## 페이지 구성 (사이드바)

| 메뉴 | 내용 |
|---|---|
| 대시보드 | KPI 4종 + 호출량/응답시간 6시간 차트 + LIVE 피드 + **시간대×인터페이스 히트맵** |
| 인터페이스 | 14건 CRUD + ▶ 즉시 실행 + 프로토콜·기관·활성 필터 |
| 호출 로그 | 12k+ 검색 (상태/프로토콜/키워드/실패만) + ↻ 단건/일괄 재처리 + **👁 상세 다이얼로그** + 🌿 lineage 체인 |
| 장애 | 미해결 카운터 + 상태 칩 + 원인/조치 이력 + 수동 해결 처리 |
| 성능 관리 | p50/p95/p99 백분위 + TPS·p95 시계열 + 가장 느린 호출 Top 10 |
| SLA | 가동률 / 평균 응답 vs 목표 (월/분기) |
| AI 분석 | LLM(OpenAI) 또는 Fallback(TF-IDF) 자동 분기, 유사 사례 Top-K |

## Ingest API (다른 시스템에서 호출 결과 보고)

Hub 가 직접 호출하지 않고 다른 내부 시스템(영업/청구/심사) 이 외부 기관과 통신한 결과도 중앙 집계할 수 있습니다.

```bash
curl -X POST http://localhost:8000/api/call-logs/ingest \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Key: $INGEST_KEY"   # backend/.env 의 INGEST_API_KEY 미설정이면 생략 가능
  -d '{
    "interface_id": 1,
    "status": "SUCCESS",
    "duration_ms": 412,
    "http_status": 200,
    "request": {"endpoint": "...", "from": "sales-system"},
    "response": {"body": {"ok": true}}
  }'
```

→ `triggered_by="ingest"` 로 표시되며 임계값 감지·WebSocket 라이브에 동일하게 반영.

## 디렉터리

```
backend/
  app/
    core/          config · DB · AES-GCM · WebSocket · KST 시간
    models/        SQLAlchemy 5종 + reprocessing/error 컬럼
    schemas/       Pydantic 입출력
    api/routes/    interfaces · executions · call_logs · incidents · sla · performance · ai · monitoring
    services/      executor (REST/SOAP/SFTP/Batch/MQ) · scheduler · detector · notifier · ai/{anomaly,rag}
    scripts/       seed_demo.py — 보험사 현실 시드
  tests/           pytest (security, executor classifier 등)
fixtures/sftp/upload/   ← atmoz/sftp 마운트, 데모 CSV 3종

frontend/
  src/
    api/           Axios + WebSocket 구독
    plugins/       Vuetify 테마
    utils/         포매터 (KST)
    views/         Dashboard · Interfaces · Logs · Incidents · Sla · Performance · AiAssistant
    stores/        Pinia 라이브 스토어
```

## 구현 하이라이트

- **재처리(Reprocessing)**: `parent_log_id` 컬럼으로 lineage 추적, `POST /retry` 단건 + `POST /bulk-retry` 필터 일괄 + `GET /chain` lineage 트리
- **에러 캡처**: 실행 시 `traceback.format_exception` 으로 풀 스택 + 응답 헤더 `call_logs.error_trace` 에 저장 → 운영자가 ELK 안 가도 원인 파악
- **시간대**: PG 세션 timezone `Asia/Seoul`, Python `now_kst()`, APScheduler KST cron, 프론트 로컬 포맷터 일관 적용
- **AI fallback**: OpenAI 키 없으면 sklearn TF-IDF char-ngram 으로 incident 검색 + 템플릿 응답 → 평가관 PC 에서도 시연 가능
- **Ingest API**: 사이드카/SDK 도입 없이도 다른 시스템 호출 결과 수집 가능, 임계값 감지·라이브 broadcast 동일 적용

## 테스트 / CI

GitHub Actions 가 push/PR 마다:
1. backend ruff lint + pytest
2. frontend type-check + production build
3. backend / frontend Docker 이미지 빌드

## 보안 메모

- `auth_secret` 컬럼은 평문이 아니라 **AES-GCM(256-bit) 암호화 후 base64** 로 저장 (`app/core/security.py`)
- 운영 배포 시 반드시 `SECRET_KEY` 를 `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` 로 새로 생성
- `INGEST_API_KEY` 설정하면 Ingest API 가 헤더 검증 (미설정 시 open)
- **계정 보안 (금감원 전자금융감독규정 권고)**
  - 비밀번호 정책: 8자 이상 + 영문·숫자·특수문자 모두 포함, 사용자명·직전과 동일 금지
  - 5회 연속 로그인 실패 시 30분 자동 잠금 (HTTP 423 LOCKED), ADMIN 즉시 해제 가능
  - 관리자 발급 임시 비밀번호 / `reset-password` 시 `must_change_password=True` → 첫 로그인 강제 변경 다이얼로그
  - 로그인 성공/실패/잠금/해제/비밀번호 변경 모두 `audit_logs` 자동 기록
  - 임계치는 `LOCKOUT_THRESHOLD` / `LOCKOUT_MINUTES` / `PASSWORD_MIN_LENGTH` / `PASSWORD_REQUIRE_COMPLEXITY` env 로 조정
- **세션 무효화** — JWT 가 stateless 임에도 즉시 폐기 가능
  - `users.session_version` 카운터 + JWT payload `sv` 비교 → 불일치 시 401 (블랙리스트 테이블/cleanup cron 불필요)
  - `/auth/logout` 본인 모든 활성 세션 종료 (다른 탭 포함), `/users/{id}/force-logout` (ADMIN) 강제 폐기 — 토큰 탈취·퇴사·권한 회수 시 즉시 격리
  - 비밀번호 변경 시 자동 +1 → 옛 토큰 무효화, 응답에 새 토큰 동봉 (UX 끊김 없음)

## Contact
박수산 · fasosan@gmail.com