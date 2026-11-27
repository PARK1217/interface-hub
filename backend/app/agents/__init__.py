"""기능별 에이전트 + 실행 플로우 (LangGraph + LangChain).

- config.py   : config/agents.yaml 읽기 (역할·도구·끝내는 기준·한도)
- llm.py      : 공통 LLM 어댑터 get_chat_model(agent_id) — LangChain 채팅 모델 + 제공사 대체
- tracing.py  : 실행 기록 (agent_runs / agent_steps) + 토큰·비용 콜백
- retriever.py: 과거 장애 TF-IDF 검색을 BaseRetriever 로 감싼 것
- tools.py    : 기존 함수를 감싼 읽기 전용 LangChain 도구
- router.py   : 안내 에이전트 (키워드 규칙 → 애매하면 LLM)
- nodes.py    : 그래프 노드 (가드 · 라우터 · 기능 에이전트 · 마무리)
- graph.py    : LangGraph 연결과 실행 함수
"""
