# 다른 프로젝트에 적용할 교훈 (Interface Hub 파일럿)

Interface Hub 를 "기능별 에이전트 + 실행 플로우"(LangGraph + LangChain)로 바꾸며 얻은 것을 다온뱅크 · stock-bot · harvest · kitten-feeding-report 에 그대로 쓰도록 정리했습니다.

## 1. 잘 된 방법

1. **기준선을 재기 전에 CI 가 실제로 도는지부터 확인.** Interface Hub 는 `ci.yml` YAML 오류로 GitHub Actions 가 40회 연속 시작조차 못 했고, ruff 오류 154개, 37바이트라 AES 키가 될 수 없는 SECRET_KEY 가 숨어 있었습니다. `gh run list` 로 이력을 먼저 보고, CI 와 같은 새 이미지(`pip install -r requirements.txt`)에서 lint·test 를 돌려 보세요.
2. **전후 비교는 같은 DB · 같은 시점에.** 데모 트래픽처럼 데이터가 계속 쌓이면 한 시간 전 기준선과 비교할 수 없습니다. 전환 전 커밋을 `git archive` 로 꺼내 **같은 DB 에 붙인 임시 컨테이너**(다른 포트)로 띄우고, 전/후를 번갈아 여러 번 재서 cold 1회차는 뺐습니다.
3. **응답 형식은 기존 생성 함수를 그대로 재사용하고 필드는 더하기만.** `make_response` 를 옮겨 쓰고 `route` · `run_id` 만 추가했더니 화면·스모크 스크립트가 거의 그대로 통과했습니다. 응답 문구도 바꾸지 않았습니다.
4. **캐시 키·통계 묶음 키는 "규칙으로 정한 분류"로 유지.** LLM 라우터 결과로 바꾸면 기존 캐시와 "자주 묻는 질문" 묶음이 깨집니다. LLM 분류는 캐시를 확인한 **뒤**, 규칙이 애매할 때만 했습니다.
5. **"애매함"을 숫자로 정의.** 키워드 점수 0 이거나 1·2위 동점일 때만 LLM 분류. 분명한 질문은 전환 전과 100% 같은 경로라 평가셋 14/14 가 유지됐습니다. `ROUTER_LLM_FALLBACK=0` 이면 완전히 전환 전 동작.
6. **도구 선택을 LLM 에 맡기지 않는 노드.** 제공사 체인에 tool-calling 을 못 하는 모델(HF)이 섞여 있어, 기능 에이전트를 "정해진 도구 순서 → LLM 1회" LangGraph 노드로 만들었습니다. 도구는 `@tool(response_format="content_and_artifact")` 로 감싸 LLM 용 글과 코드용 값을 함께 받고, 호출마다 trace 단계가 남습니다.
7. **도구 읽기 전용을 코드로 강제.** 도구 세션에 `before_flush`(ORM 쓰기)와 `do_orm_execute`(SELECT/WITH 외 SQL) 차단을 걸고, 테스트로 ① 금지 함수가 도구 목록에 없음 ② 도구 소스에 금지 함수·`.commit(` 이 없음 ③ 읽기 전용 세션이 쓰기를 막음을 확인했습니다.
8. **키 없이 토큰·비용 경로 검증.** `GenericFakeChatModel` 에 `usage_metadata`·`response_metadata` 를 넣은 `AIMessage` 를 주면 콜백·비용·백오피스까지 실제 경로로 검증됩니다. 화면 캡처에는 "가짜 모델로 찍음"을 밝혔습니다.
9. **콜백이 어느 제공사 호출인지 알게 metadata 를 직접 붙이기.** `model.with_config(metadata={"agent_provider": p, "agent_model": m})`. `ls_provider` 는 가짜 모델·커스텀 모델에서 값이 달라 대체 체인의 시도 기록(`llm_attempts`)을 맞추기 어렵습니다.
10. **기존 스모크 스크립트가 바꿔 끼우던 지점을 새 구조에도 하나 남기기.** `build_chat_model(provider, model)` 을 모듈 함수로 두어, httpx 내부 함수를 바꿔 끼우던 스크립트를 한 군데만 고쳐 살렸습니다.
11. **실행 기록은 요청 끝에 한 트랜잭션.** 시작 시 INSERT + 단계마다 flush 는 요청당 +27ms 였고, 끝에 `agent_runs` + `agent_steps` + 기존 로그를 같은 연결에서 한 번에 쓰니 +18ms 로 줄었습니다.
12. **sqlite 메모리 DB 픽스처 + PG 전용 함수만 바꿔 끼우기.** `StaticPool` 엔진으로 `create_all` 하고, `date_part` · `NULLS LAST` 를 쓰는 기존 함수는 도구 모듈 속성으로 참조해 테스트에서 바꿔 끼웠습니다. CI 는 네트워크·Redis 없이 그래프 전체를 돕니다.

## 2. 막힌 점

1. **의존성 충돌: `langchain-core` 1.x → `langsmith` → `websockets>=15`.** 기존 `websockets==14.1` 고정과 충돌. uvicorn 0.34 가 쓰는 `websockets.legacy` 가 15.0.1 에 있는지 확인하고 올렸습니다. `pip install --dry-run -r requirements.txt` 를 먼저 돌리세요 (전체 해석은 몇 분 걸림).
2. **`langchain-mistralai` 가 `tokenizers` · `huggingface_hub` 를 끌어옴** — 이미지가 커집니다.
3. **기존 인라인 마이그레이션이 한 트랜잭션.** 마지막 `INSERT` 실패가 그 뒤에 더한 `ALTER` 를 조용히 무시하게 만들어, 새 컬럼이 안 생겼는데 경고만 찍혔습니다. 문장별 SAVEPOINT 로 고쳤습니다. 다른 프로젝트도 "스킵" 경고 로그를 꼭 확인하세요.
4. **`uvicorn --reload` 중 파일을 고치면** 시작 시 ALTER 와 다른 쿼리가 교착. 개발 중에만 생기는 일이지만 원인 파악에 시간이 들었습니다.
5. **LLM 키가 없음.** 실제 usage 값이 들어오는지는 확인하지 못했고, 환율 1차 출처(한국은행 ECOS)는 동적 페이지라 자동으로 읽지 못해 `USD_KRW_RATE` 를 비워 뒀습니다(비용은 "단가 확인 필요").
6. **오래된 기본 모델 이름.** `claude-3-5-sonnet-20240620` 은 2025-10-28 종료. 단가를 확인하다 발견했습니다. 각 프로젝트의 기본 모델이 아직 살아 있는지 공식 deprecation 페이지로 확인하세요.
7. **데이터 의존 스모크 스크립트.** `check_ai_repeat_block.py` 는 무관한 질문이 no_match 이길 기대하는데, 데모 데이터가 쌓이며 유사도가 임계 0.05 를 넘었습니다. 전환 전 코드로도 같은 값(0.052)이 나와 회귀가 아님을 증명하는 데 시간이 들었습니다.
8. **환경 문제들.** 다른 프로젝트 컨테이너와 포트 충돌(6379) → compose 프로젝트 이름 + 임시 override 파일. 스모크 스크립트가 `docker compose exec backend` 를 써서 `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE` 지정 필요. Windows Git Bash 는 `docker exec -w /app` 경로를 바꿔 버려 `MSYS_NO_PATHCONV=1` 필요.
9. **LangChain 1.x 의 `AIMessage.text`** 는 문자열이면서 호출도 되는 값이라 `callable` 검사로 부르면 경고가 납니다. `isinstance(text, str)` 를 먼저 보세요.
10. **sqlite 는 시간대 없는 datetime** 을 돌려줘 KST 비교 코드(IsolationForest)가 깨집니다 — 테스트에서 바꿔 끼움.
11. **`.env.example` 의 빈 숫자 값.** `USD_KRW_RATE=` 를 그대로 복사하면 pydantic 이 숫자 변환에 실패 → 빈 문자열을 None 으로 받는 validator 필요.

## 3. 기준 문서(AGENT_STANDARD.md · templates)에 고칠 점

1. **수준별 모델 변수 형식.** 제공사 대체 체인이 2곳 이상이면 `AGENT_MODEL_FAST=모델이름` 하나로 정할 수 없습니다. `"제공사:모델,제공사:모델"` 목록 형식(이름만 쓰면 모든 제공사)을 표준으로 넣어 주세요.
2. **`LLM_PROVIDER_CHAIN` 은 "기존 변수가 없을 때만"** 이라고 본문 표에도 적어 주세요 (Interface Hub 는 `AI_PROVIDER`/`AI_FALLBACK_CHAIN` 재사용).
3. **`agent_runs` 에 `outcome` 컬럼 추가 권장.** 양식의 status(ok/handoff/refused/error)만으로는 "찾지 못함 / 되묻기 / 근거 부족 / LLM 실패 후 대체 답"을 구분할 수 없습니다. 기존 결과 코드를 그대로 남기는 칸이 필요합니다.
4. **3장 "도구 호출 루프(또는 create_react_agent)"에 대안 명시.** 작은 모델·tool-calling 미지원 제공사가 섞이면 "정해진 도구 순서 + LLM 1회 노드"를 허용(권장)한다고 적어 주세요. `max_steps` 는 도구+LLM 호출 수로 세면 됩니다.
5. **6장 ① 기준선에 추가:** "CI 가 실제로 통과하는지(Actions 이력) 확인", "CI 와 같은 새 이미지에서 lint·test", "스모크 스크립트 실행 조건(compose 프로젝트 이름 등) 기록".
6. **6장 ④ 전후 비교에 추가:** 데이터가 계속 쌓이는 프로젝트는 "전환 전 커밋을 같은 DB 에 띄워 같은 시점에 번갈아 측정, cold 1회차 제외".
7. **실행 기록 저장 시점의 트레이드오프 명시.** 요청 끝 한 트랜잭션은 빠르지만 실행 중(running) 상태가 보이지 않습니다. 긴 배치형 에이전트(stock-bot 등)는 시작 시 1행을 먼저 쓰는 쪽이 낫습니다.
8. **키 없을 때 토큰·비용 검증 방법을 표준화.** "가짜 채팅 모델 + usage_metadata 로 테스트·화면 확인, 캡처에 그 사실 표기, 실제 키 확인은 남은 일로" 를 7장 완료 조건 2의 허용 방법으로 적어 주세요.
9. **`llm_prices.yaml` 에 환율 출처 칸.** `usd_krw_rate_source`(URL)·`usd_krw_rate_checked_at` 를 두고, 1차 출처를 못 읽으면 null 로 두라고 명시. 모델별 `status`(active/deprecated/retired) 칸도 있으면 종료된 기본 모델을 일찍 잡습니다.
10. **백오피스 권한 지침.** 실행 기록에는 다른 사용자의 질문 요약이 보이므로 "기존 감사 로그와 같은 권한"을 기본으로 하라고 적어 주세요.
11. **README 양식의 스크린샷 경로**(`agent-trace.png`)를 형식 자유로 (이번엔 브라우저 캡처가 jpg).
12. **브랜치 규칙 충돌.** 8장은 `feat/agent-flow` 이지만 세션 워크트리는 자기 브랜치(`claude/agent-flow-…`)를 받습니다. 어느 쪽을 따를지 한 줄로 정해 주세요.
13. **`AI_DAILY_BUDGET_USD`** 는 env 양식에만 있고 본문에 동작 정의가 없습니다 (이번엔 구현하지 않음). 넣을 거면 "닿으면 어떤 응답을 주는지"를 정해 주세요.
14. **기존 로그 테이블과의 연결 방향.** "기존 로그 테이블에 `run_id` 를 더한다(기존 행·API 유지)" 를 기본안으로 적으면 다온뱅크 `AI_LLM_CALL_LOG` 도 같은 방식으로 빨리 정할 수 있습니다.
