# Interface Hub · Claude 작업 규칙

## 에이전트 전환
- 기준 문서: 에이전트 전환 기준 `AGENT_STANDARD.md` (이 저장소 밖, 포트폴리오 공통) — 작업 전에 읽고 그대로 따릅니다.
- README 양식: 같은 기준의 `templates/README_TEMPLATE.md`
- 설정 양식: 같은 기준의 `templates/` (agents.yaml, llm_prices.yaml, agent_trace_schema.sql, env.agent.example)
- 이 프로젝트의 구성: `backend/config/agents.yaml`, 코드 `backend/app/agents/`, 전환 기록 `docs/agent-migration/`

## 브랜치·배포
- 에이전트 전환 작업 브랜치에서만 작업, 단계마다 커밋 (파일럿은 세션 워크트리 브랜치 `claude/agent-flow-9b3714`)
- push·배포·main 병합은 하지 않음 (사용자가 직접)

## 절대 지킬 것
- `.env` 값 출력·커밋 금지. 새 변수는 `backend/.env.example`에 이름만
- 이 폴더 밖의 파일 수정 금지
- 다음 함수는 에이전트 도구에 넣지 않음: `execute_interface`, 재처리(`retry_call_log`, `bulk_retry`, `retry_related`), 장애 해결·수정(`resolve_incident`, `update_incident`, `mark_related_handled`), 인터페이스 생성·수정·삭제·음소거·복구, `reveal_secret`, `dispatch_alert`, `check_expiring_secrets`, `sync_jobs`, 알림 룰·사용자 관리, `record_audit`, `cache.invalidate_all` (전체 목록: `backend/config/agents.yaml` `forbidden_tools`)
- 에이전트 도구는 읽기 전용 세션(`app/agents/tools.readonly_session`)으로만 DB 를 읽음
- 실제 고객·금융 데이터 파일은 읽어 기록하지 않음: 이 프로젝트의 데이터는 `seed_demo` 합성 데이터와 `fixtures/` 데모 CSV 뿐

## 명령어
- 설치/실행: `docker compose up -d --build` (처음 한 번 `docker compose exec backend python -m app.scripts.seed_demo`)
- 테스트: `docker compose exec -e DATABASE_URL=sqlite+pysqlite:///:memory: backend pytest -q tests` · 린트 `ruff check .` (backend) · `npm run type-check && npm run build` (frontend)
- 평가셋 실행: `docker compose exec backend python eval/run_agent_eval.py --phase after`
- 스모크: `python scripts/check_ai_*.py` (실행 중인 백엔드 대상, 일부는 `docker compose exec backend` 사용)

## 보고
- 모든 보고는 한국어
- 단계를 마칠 때마다 "3/6단계 끝 · 지금까지 N분"처럼 짧게 알림
