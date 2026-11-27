# 전환 전 기준선 (before)

- 측정일: 2026-11-27
- 커밋: `e471d66` (에이전트 전환 전 main 과 같음)
- 환경: Docker (`python:3.12-slim` 백엔드 + postgres:15 + redis:7), `seed_demo` 시드 + 데모 트래픽
- LLM: **키 없음 → `AI_PROVIDER=fallback`** (모든 답이 fallback 모드). 실제 LLM 호출 비교는 키가 생긴 뒤 다시 잽니다.
- 캐시: 측정 전에 Redis 를 비워 첫 호출(캐시 미적중)을 잼

## 1. 평가셋 (`backend/eval/agent_eval_set.yaml`, 14문항 · 전환 후 RCA 2문항 추가 예정)

```
docker exec ihub-agent-backend python eval/run_agent_eval.py --phase before
```

| id | intent | outcome | mode | 시간(ms) | 토큰(입/출) | 판정 |
|---|---|---|---|---:|---|---|
| q01 | stats_query | success | fallback | 124 | 기록 없음 | ✓ |
| q02 | stats_query | success | fallback | 81 | 기록 없음 | ✓ |
| q03 | stats_query | success | fallback | 81 | 기록 없음 | ✓ |
| q04 | stats_query | success | fallback | 92 | 기록 없음 | ✓ |
| q05 | config_query | success | fallback | 19 | 기록 없음 | ✓ |
| q06 | config_query | success | fallback | 18 | 기록 없음 | ✓ |
| q07 | config_query | success | fallback | 17 | 기록 없음 | ✓ |
| q08 | case_lookup | success | fallback | 4267 | 기록 없음 | ✓ |
| q09 | case_lookup | success | fallback | 150 | 기록 없음 | ✓ |
| q10 | case_lookup | success | fallback | 132 | 기록 없음 | ✓ |
| q11 | case_lookup | no_match | fallback | 93 | 기록 없음 | ✓ |
| q12 | general | no_match | fallback | 61 | 기록 없음 | ✓ |
| q13 | general | empty_question | fallback | 10 | 기록 없음 | ✓ |
| q14 | general | success | fallback | 76 | 기록 없음 | ✓ |

- intent 적중 14/14 · outcome 적중 14/14
- 응답 시간 평균 373ms · 중앙값 81ms (q08 은 첫 sklearn import 비용 포함)
- 토큰·비용: **기록 없음** — 기존 `llm.py` 가 응답의 usage 를 버리고, `ai_query_logs` 에 토큰 컬럼이 없음

## 2. 기존 테스트

| 항목 | 결과 |
|---|---|
| `pytest -q tests/test_security.py tests/test_detector.py` (sqlite) | 3 passed |
| `ruff check .` (backend) | **154개 오류** (B008 125개 = FastAPI `Depends`/`Query` 기본값, 나머지 import 정렬·미사용 import 등) |
| `npm run type-check` | 통과 |
| `npm run build` | 통과 (500kB 넘는 청크 경고만) |
| GitHub Actions CI | **최근 40회 모두 실패** — `ci.yml:31` 의 `DATABASE_URL: sqlite+pysqlite:///:memory:` 가 따옴표 없이 `:` 로 끝나 YAML 파싱 오류("workflow file issue"). 잡이 한 번도 시작되지 않음 |

→ "CI 가 계속 통과" 하려면 먼저 CI 를 살려야 합니다. 전환 작업 첫 커밋에서 `ci.yml` 따옴표 + ruff 설정(FastAPI 호출 허용)·자동 수정으로 고칩니다.

## 3. AI 스모크 스크립트 (`scripts/check_ai_*.py`, 9개)

| 스크립트 | 결과 | 메모 |
|---|---|---|
| check_ai_cache.py | 통과 | |
| check_ai_errors.py | **1개 실패** | "정상 호출" 단계가 fallback 모드에서 `llm_error` 를 기대하지만 기존 코드는 키가 없으면 `llm_error=None` (전환 전부터 있던 실패) |
| check_ai_fallback_chain.py | 통과 | `docker compose exec backend` 사용 → `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE` 지정 필요 |
| check_ai_heatmap.py | 통과 | `app` import → 컨테이너 안에서 실행 |
| check_ai_intent.py | 통과 | |
| check_ai_reasons.py | 통과 | |
| check_ai_repeat_block.py | 통과 | |
| check_ai_stats_extended.py | 통과 | 컨테이너 안에서 실행 |
| check_ai_suggest_valid.py | 통과 | |

## 4. 측정 방법 메모

- 다른 프로젝트 컨테이너와 포트가 겹쳐(6379 등) 별도 프로젝트 이름 `ihub-agent` + 임시 compose override(포트·컨테이너 이름만 변경)로 띄웠습니다. 사용자의 기존 `interface-hub_postgres_data` 볼륨은 건드리지 않았습니다.
