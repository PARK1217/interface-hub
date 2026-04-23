# NOA Interface Hub

> 보험사 외부 인터페이스를 한 곳에서 **등록 · 실행 · 감시 · 분석**하는 통합 관제 플랫폼.
> 노아에이티에스(주) 공채 15기 2차 — 박수산.

흩어진 REST/SOAP/FTP/MQ 연동을 하나의 UI 로 모으고, 실시간 모니터링과 AI(RAG) 기반 장애 분석으로 운영자 부담을 줄이는 것이 목표입니다.

---

## 핵심 기능

| 구분 | 기능 | Phase |
|---|---|---|
| F1 | 인터페이스 등록·실행 (REST/SOAP/FTP/MQ, Cron, AES-GCM 시크릿) | 1 |
| F2 | 실시간 모니터링 (WebSocket 라이브 + ApexCharts) | 1 |
| F3 | 장애 자동 감지·분류 (Timeout/Auth/Format/5xx, Slack+Email) | 2 |
| F4 | 로그 검색 · SLA 리포트 | 2 |
| F5 | ML 이상 탐지 (Isolation Forest) + RAG 챗봇 (LangChain · FAISS) | 3 |

## 아키텍처

```
┌──────────────┐  HTTPS / WS   ┌──────────────────────┐  HTTP   ┌──────────────┐
│ Vue 3 + Vite │ ◀───────────▶ │ FastAPI (Python 3.12) │ ◀────▶ │ 외부 기관 API │
│ Vuetify 3    │               │  · CRUD / Scheduler   │         └──────────────┘
│ ApexCharts   │               │  · WebSocket / RAG    │
└──────────────┘               └─────────┬─────────────┘
                                         │
                            ┌────────────┴────────────┐
                            │                         │
                     PostgreSQL 15               Redis 7  (queue/cache)
                       (5 tables)               FAISS index (disk)
```

5개 핵심 테이블: `interfaces` · `call_logs` · `incidents` · `sla_targets` · `vector_cases`.

## 빠른 시작 (Docker)

```bash
# 1) 환경 변수 준비
cp backend/.env.example backend/.env
# (선택) AI 사용 시 OPENAI_API_KEY, Slack/SMTP 값 채우기

# 2) 전체 스택 기동
docker compose up --build

# 3) 접속
#   · Frontend  http://localhost:5173
#   · Backend   http://localhost:8000/docs   (Swagger)
#   · Health    http://localhost:8000/health
```

## 로컬 개발 (Docker 없이)

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/activate          # Windows
pip install -r requirements.txt
export DATABASE_URL=sqlite+pysqlite:///./dev.db          # SQLite로 빠른 시작
uvicorn app.main:app --reload

# Frontend (다른 터미널)
cd frontend
npm install
npm run dev
```

## 디렉터리

```
backend/
  app/
    core/          config · DB · AES-GCM 보안 · WebSocket 매니저
    models/        SQLAlchemy 모델 5종
    schemas/       Pydantic 입출력 스키마
    api/routes/    interfaces · executions · call_logs · incidents · sla · ai · monitoring(ws)
    services/      executor · scheduler · detector · notifier · ai/{anomaly,rag}
  tests/           pytest (security, detector classifier 등)

frontend/
  src/
    api/           Axios client · WebSocket 구독
    plugins/       Vuetify 테마
    views/         Dashboard · Interfaces · Logs · Incidents · Sla · AiAssistant
    stores/        Pinia 라이브 스토어
```

## Phase 로드맵

- **Phase 1 (MVP)** — 인터페이스 CRUD, 수동/Cron 실행, 호출 로그 수집, 실시간 대시보드. ✅ 구현 완료
- **Phase 2 (CORE)** — 임계값 자동 감지, 오류 유형 분류, Slack/Email 알림, SLA 리포트. ✅ 구현 완료
- **Phase 3 (AI BOOST)** — Isolation Forest 이상 탐지, FAISS RAG 챗봇 (`/api/ai/ask`). ✅ 골격 완료 (실전 사용 시 OPENAI_API_KEY 필요)

## 테스트 / CI

GitHub Actions가 push/PR마다:

1. backend ruff lint + pytest
2. frontend type-check + production build
3. backend / frontend Docker 이미지 빌드

## 보안 메모

- `auth_secret` 컬럼은 평문이 아니라 **AES-GCM(256-bit) 암호화 후 base64**로 저장됩니다 (`app/core/security.py`).
- 운영 배포 시 반드시 `SECRET_KEY` 를 `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` 로 새로 생성하세요.

## Contact
박수산 · fasosan@gmail.com