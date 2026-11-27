# 전환 후 측정 (after) · 전후 비교

- 측정일: 2026-11-27
- 커밋: `148f426` (브랜치 `claude/agent-flow-9b3714`)
- 환경: before.md 와 같음 (Docker · seed_demo · 데모 트래픽 · **LLM 키 없음 → fallback 모드**)
- 공정 비교: 전환 전 코드(`5f7c458`)를 **같은 DB 에 붙은 임시 컨테이너**로 함께 띄우고, 같은 시점에 전/후를 번갈아 3회씩 실행.
  1회차는 기동 직후(cold)라 빼고 2·3회차의 문항별 중앙값을 씀. (before.md 의 첫 측정은 한 시간 전이라 데이터 양이 달랐음)

## 1. 평가셋 (`backend/eval/agent_eval_set.yaml`, 14문항 + 전환 후 RCA 2문항)

```
docker exec ihub-agent-backend python eval/run_agent_eval.py --phase after
```

| id | intent | route (일한 에이전트) | outcome | mode | 판정 |
|---|---|---|---|---|---|
| q01–q04 | stats_query | stats_agent | success | fallback | ✓ ×4 |
| q05–q07 | config_query | config_agent | success | fallback | ✓ ×3 |
| q08–q10 | case_lookup | case_agent | success | fallback | ✓ ×3 |
| q11 | case_lookup | case_agent | no_match | fallback | ✓ |
| q12 | general | case_agent | no_match | fallback | ✓ |
| q13 | general | guard (앞단 가드) | empty_question | fallback | ✓ |
| q14 | general | case_agent | success | fallback | ✓ |
| r01 | root_cause | rca_agent (장애 #18) | success | fallback (근거 요약) | ✓ 신규 |
| r02 | root_cause | rca_agent (장애 #2) | success | fallback (근거 요약) | ✓ 신규 |

## 2. 전후 비교표

| 항목 | 전 | 후 | 메모 |
|---|---|---|---|
| intent 적중 (14문항) | 14/14 | 14/14 | 키워드 규칙·우선순위 그대로 |
| outcome 적중 (14문항) | 14/14 | 14/14 | 거절(empty_question)·찾지 못함(no_match) 판정 그대로 |
| 경로(route) 적중 | - (필드 없음) | 16/16 | 새 필드. RCA 2문항 포함 |
| 평균 응답 시간 (14문항, 같은 시점) | 47ms | 65ms | **+18ms** — 아래 3번 |
| 중앙값 응답 시간 | 55ms | 59ms | |
| 요청당 평균 토큰 | 기록 없음 | 0 (LLM 미호출) | 전에는 usage 를 버려 기록 불가. 후에는 응답 usage 그대로 기록 (가짜 모델로 검증, 4번) |
| 요청당 평균 비용(원) | 기록 없음 | 0 | 환율 미확인 → LLM 을 부르면 "단가 확인 필요" |
| 거절·되묻기·찾지 못함 정확도 | 2/2 (no_match, empty_question) | 2/2 + 되묻기 테스트 통과 | 되묻기(need_target)는 단위 테스트로 확인 |
| 장애 원인 분석 | 없음 | 2/2 | 신규 (장애 화면 "원인 분석" 버튼, `/api/ai/rca`) |

문항별 응답 시간(ms, 2·3회차 중앙값)

| | q01 | q02 | q03 | q04 | q05 | q06 | q07 | q08 | q09 | q10 | q11 | q12 | q13 | q14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 전 | 58 | 60 | 66 | 79 | 20 | 17 | 17 | 67 | 54 | 61 | 56 | 48 | 11 | 53 |
| 후 | 80 | 96 | 111 | 125 | 39 | 43 | 34 | 61 | 65 | 58 | 58 | 55 | 18 | 67 |

RCA(신규) 응답 시간: 860~1,180ms (도구 6개 — SLA 30일 집계와 IsolationForest 학습이 대부분)

## 3. 나빠진 항목과 이유

- **응답 시간 +18ms(평균)** — 통계·설정 질문에서 +20~45ms.
  - 원인: LangGraph 실행 + 도구 호출을 스레드로 넘기는 비용 + 요청마다 실행 기록(`agent_runs` 1행 + `agent_steps` 6~10행)을 쓰는 트랜잭션.
  - 줄인 것: 처음에는 요청 시작 때 1번, 끝날 때 단계마다 flush 하던 것을 **요청 끝에 한 트랜잭션**(실행 기록 + ai_query_logs 같은 연결)으로 바꿈 (커밋 `fa8ee35`). 바꾸기 전 측정은 중앙값 +27ms 였음.
  - 판단: 사람 대상 질의(수십 ms 단위)라 체감 차이는 없다고 봄. 기록이 필요 없으면 `AGENT_TRACE_ENABLED=0`.
- 사례 검색(q08~q12)은 차이 없음 (TF-IDF 계산이 대부분이라 상대적으로 작음).

## 4. 토큰·비용 기록 확인 (LLM 키 없음 → 가짜 채팅 모델)

실제 키가 없어 실제 LLM 호출로는 확인하지 못했습니다. 대신 **그래프·도구·기록 경로는 그대로 두고 채팅 모델만 가짜(응답 usage 고정값)로 바꿔 끼워** 확인했습니다.

- 자동 테스트: `tests/test_agent_llm.py`(대체 체인·usage 그대로·단가 없으면 NULL·빈 응답 다음 제공사), `tests/test_agent_flow.py::test_rca_with_llm_records_tokens`
- 화면 확인: 검증용 스택에서 RCA 1건·통계 1건을 가짜 모델로 실행 → `/agent-runs` 에서 LLM 단계 `openai · gpt-4o-mini`, 토큰 1,834/212, 비용 "단가 확인 필요"(USD_KRW_RATE 미설정) 표시 확인 (`docs/screenshots/agent-trace.jpg`)
- **남은 확인**: 실제 키로 1회 이상 호출해 usage 값이 들어오는지 (Mistral/Anthropic/OpenAI 는 LangChain 이 usage_metadata 를 채움, HF 는 토큰 미제공)

## 5. 기존 테스트 · CI

| 항목 | 전 | 후 |
|---|---|---|
| pytest | 3 passed (2개 파일) | **44 passed** (8개 파일: 기존 2 + 에이전트 6) |
| ruff | 154개 오류 | 0 |
| npm type-check / build | 통과 | 통과 (mermaid 는 동적 import 로 별도 청크) |
| CI 와 같은 새 이미지(`python:3.12-slim` + `pip install -r requirements.txt`)에서 ruff + pytest | - | 통과 (`pip check` 문제 없음) |
| GitHub Actions | 40회 연속 실패 (YAML 오류) | YAML·SECRET_KEY 수정 — **push 하지 않아 실제 Actions 실행은 미확인** |

## 6. AI 스모크 스크립트 (`scripts/check_ai_*.py`, 9개)

| 스크립트 | 전 | 후 | 메모 |
|---|---|---|---|
| check_ai_cache.py | 통과 | 통과 | |
| check_ai_errors.py | 1개 실패 | 1개 실패 | 같은 실패 (fallback 모드에서 llm_error 를 기대 — 전환 전부터) |
| check_ai_fallback_chain.py | 통과 | 통과 | 사라진 httpx 내부 함수(`L._openai`) 대신 `app.agents.llm.build_chat_model` 을 바꿔 끼우도록 스크립트 수정. mistral 잘못된 키 → 실제 ChatMistralAI 401 → `auth_failed` 확인 |
| check_ai_heatmap.py | 통과 | 통과 | |
| check_ai_intent.py | 통과 | 통과 | |
| check_ai_reasons.py | 통과 | 통과 | 잘못된 키 401 → auth_failed (LangChain 예외 분류) |
| check_ai_repeat_block.py | 통과 | **4개 실패** | 회귀 아님 — 아래 |
| check_ai_stats_extended.py | 통과 | 통과 | |
| check_ai_suggest_valid.py | 통과 | 통과 | |

`check_ai_repeat_block.py` 실패 원인: 이 스크립트는 "강아지 산책 시간 원인 알려줘 {시각}" 이 no_match(유사도 < 0.05)가 되기를 기대합니다.
측정 사이에 데모 트래픽이 해결된 장애를 132건까지 늘려 같은 질문의 Top-1 유사도가 0.052 가 됐습니다.
**전환 전 검색 코드를 같은 데이터에 돌려도 0.052** 로 같아(커밋 `e471d66` 의 `_retrieve` 를 직접 실행) 데이터 의존적인 기존 스크립트의 흔들림으로 판단했습니다.
같은 의미의 검증은 고정 데이터를 쓰는 `tests/test_agent_flow.py::test_case_agent_and_no_match` 가 통과합니다.

## 7. 전환하며 함께 고친 기존 문제

- `database.py` 데모 마이그레이션: 모든 ALTER 가 한 트랜잭션이라 마지막 `INSERT alert_rules` 실패가 그 뒤에 더한 줄을 모두 무시하게 만들었음 → 문장별 SAVEPOINT
- `database.py` PG 전용 `connect_args` 를 PG 일 때만 (sqlite 테스트)
- Anthropic 기본 모델 `claude-3-5-sonnet-20240620`(2025-10-28 종료) → `claude-sonnet-5-5`
- CI: YAML 오류, ruff 154개, 37바이트 SECRET_KEY
