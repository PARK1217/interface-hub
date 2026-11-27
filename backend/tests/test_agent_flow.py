"""그래프 전체 — 라우터 → 기능 에이전트 → 결과 + 실행 기록 (sqlite, 네트워크·Redis 없음)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.agents import llm as agent_llm
from app.agents import tools, tracing
from app.agents.graph import mermaid, run_ask, run_rca
from app.core.config import Settings
from app.core.time import now_kst
from app.models import AgentRun, AgentStep, AiQueryLog, CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType
from app.services.ai import cache as ai_cache
from app.services.ai import llm as legacy


@pytest.fixture
def world(sqlite_db, monkeypatch):
    """인터페이스 1개 + 호출 로그 + 해결된 과거 장애 2건 + 열린 장애 1건. Redis·PG 전용 SQL 은 가짜로."""
    store: dict[str, dict] = {}

    async def get_cached(k):
        return store.get(k)

    async def set_cached(k, v):
        store[k] = v

    monkeypatch.setattr(ai_cache, "get_cached", get_cached)
    monkeypatch.setattr(ai_cache, "set_cached", set_cached)
    # PG 전용 SQL(date_part, NULLS LAST)을 쓰는 기존 함수는 가짜로 (도구가 감싸는 경로는 그대로)
    monkeypatch.setattr(tools, "build_stats_context", lambda db, days, top_n: "| 인터페이스 | 실패율 |\n| KIDI | 12% |")
    import app.api.routes.performance as perf

    class _Row:
        def __init__(self, iid):
            self.interface_id = iid

        def model_dump(self, mode="json"):
            return {"retry_max": 2, "total_calls": 40, "multi_attempt_calls": 5, "recovered_calls": 3,
                    "failed_after_retry": 2, "avg_attempts": 1.2}

    monkeypatch.setattr(perf, "retry_effects", lambda days, only_with_policy, db: [_Row(1)])
    # sqlite 는 시간대 없는 datetime 을 돌려줘 IsolationForest 함수의 시각 비교가 안 됨 → 가짜 점수
    monkeypatch.setattr(tools, "score_anomalies", lambda db, iid: (-0.05, True))
    no_llm = Settings(ai_provider="fallback")
    monkeypatch.setattr(legacy, "get_settings", lambda: no_llm)

    now = now_kst()
    with sqlite_db() as db:
        itf = Interface(id=1, name="보험개발원-실손중복청구확인", organization="보험개발원 (KIDI)", endpoint="https://kidi/api",
                        method="POST", response_ms_threshold=1000)
        db.add(itf)
        for i in range(40):
            db.add(CallLog(interface_id=1, status=CallStatus.SERVER_ERROR if i % 3 == 0 else CallStatus.SUCCESS,
                           http_status=503 if i % 3 == 0 else 200, duration_ms=300 + i,
                           called_at=now - timedelta(minutes=i), triggered_by="schedule"))
        db.add(Incident(id=10, interface_id=1, type=IncidentType.TIMEOUT, severity="warning",
                        summary="보험개발원 (KIDI) 응답 지연으로 타임아웃 다발", root_cause="전용회선 패킷 손실",
                        resolution="우회 경로 전환", detected_at=now - timedelta(days=20), resolved_at=now - timedelta(days=19)))
        db.add(Incident(id=11, interface_id=1, type=IncidentType.AUTH_ERROR, severity="warning",
                        summary="보험개발원 (KIDI) 401 인증 오류 다발", root_cause="API 키 만료", resolution="키 재발급",
                        detected_at=now - timedelta(days=10), resolved_at=now - timedelta(days=9)))
        db.add(Incident(id=12, interface_id=1, type=IncidentType.SERVER_ERROR, severity="critical",
                        summary="보험개발원 (KIDI) 측 5xx 응답 급증", detected_at=now - timedelta(minutes=45)))
        db.commit()
    return sqlite_db


def _run(factory, run_id):
    with factory() as db:
        run = db.get(AgentRun, run_id)
        steps = db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)).all()
        return run, steps


async def test_stats_agent(world):
    import uuid

    res = await run_ask("지금 가장 장애가 잦은 인터페이스가 뭐야?", actor_user_id=None)
    assert res["route"] == "stats_agent" and res["intent"] == "stats_query" and res["mode"] == "fallback"
    assert "KIDI | 12%" in res["answer"]
    run, steps = _run(world, uuid.UUID(res["run_id"]))
    assert (run.route, run.status, run.outcome, run.entry) == ("stats_agent", "ok", "success", "chat")
    kinds = [(s.kind, s.name) for s in steps]
    assert ("router", "router") in kinds and ("agent", "stats_agent") in kinds and ("tool", "get_ops_stats") in kinds
    tool_step = next(s for s in steps if s.kind == "tool")
    agent_step = next(s for s in steps if s.kind == "agent")
    assert tool_step.parent_step_id == agent_step.step_id
    with world() as db:  # 기존 ai_query_logs 와 연결
        log = db.scalar(select(AiQueryLog))
        assert log.run_id == run.run_id and log.outcome == "success"


async def test_config_agent(world):
    res = await run_ask("보험개발원 인터페이스 엔드포인트 url 이 뭐야?")
    assert res["route"] == "config_agent" and "보험개발원-실손중복청구확인" in res["answer"]


async def test_case_agent_and_no_match(world):
    res = await run_ask("보험개발원 API 타임아웃 원인과 조치 방법은?")
    assert res["route"] == "case_agent" and res["outcome"] == "success"
    assert res["similar_cases"][0]["incident_id"] == 10

    res = await run_ask("강아지 산책 시간이 매번 달라지는 원인을 알려줘")
    assert res["outcome"] == "no_match" and res["mode"] == "fallback"
    assert "관련된 과거 장애 사례를 찾지 못해" in res["answer"]
    with world() as db:  # 사용자 입력 결함은 ai_query_logs 에 남기지 않음 (전환 전과 같음)
        assert db.scalar(select(AiQueryLog).where(AiQueryLog.outcome == "no_match")) is None


async def test_short_question_guard(world):
    res = await run_ask("장애")
    assert res["route"] == "guard" and res["outcome"] == "empty_question"


async def test_rca_fallback_collects_evidence(world):
    res = await run_rca(12)
    assert res["route"] == "rca_agent" and res["intent"] == "root_cause" and res["outcome"] == "success"
    assert "HTTP 5xx" in res["answer"] and "재시도" in res["answer"]
    import uuid

    run, steps = _run(world, uuid.UUID(res["run_id"]))
    tool_names = [s.name for s in steps if s.kind == "tool"]
    assert tool_names == ["get_incident_detail", "get_recent_call_logs", "get_retry_stats", "get_sla_status",
                          "get_anomaly_score", "search_similar_cases"]
    assert run.entry == "incident"


async def test_rca_chat_without_target_clarifies(world, monkeypatch):
    # LLM 라우터가 root_cause 를 골랐지만 질문에 인터페이스가 없음 → 되묻기
    from app.agents import router

    async def fake(agent_id, prompt, *, system=None, tracker=None):
        return legacy.LLMResponse(provider="fake", model="fake", content="root_cause"), []

    monkeypatch.setattr(router, "is_configured", lambda: True)
    monkeypatch.setattr(router, "ainvoke_llm", fake)
    res = await run_ask("우리 회사 시스템 좀 봐줄래")
    assert res["route"] == "clarify" and res["outcome"] == "need_target"


async def test_rca_with_llm_records_tokens(world, monkeypatch):
    s = Settings(ai_provider="openai", openai_api_key="k", usd_krw_rate=1400.0)
    monkeypatch.setattr(legacy, "get_settings", lambda: s)
    monkeypatch.setattr(tracing, "get_settings", lambda: s)
    tracing._price_table.cache_clear()
    msg = AIMessage(content="1) 원인 가설: 상대 기관 5xx [#11](/incidents?focus=11)",
                    usage_metadata={"input_tokens": 900, "output_tokens": 120, "total_tokens": 1020},
                    response_metadata={"model_name": "gpt-4o-mini"})
    monkeypatch.setattr(agent_llm, "build_chat_model", lambda p, m: GenericFakeChatModel(messages=iter([msg])))
    res = await run_rca(12)
    assert res["mode"] == "llm" and res["provider"] == "openai" and "원인 가설" in res["answer"]
    import uuid

    run, steps = _run(world, uuid.UUID(res["run_id"]))
    llm = [x for x in steps if x.kind == "llm"]
    assert len(llm) == 1 and (llm[0].input_tokens, llm[0].output_tokens) == (900, 120)
    assert llm[0].agent_id == "rca_agent" and llm[0].model == "gpt-4o-mini"
    assert (run.total_input_tokens, run.total_output_tokens) == (900, 120)
    assert float(run.total_cost_krw) > 0


def test_mermaid_has_all_agents():
    m = mermaid()
    for n in ("guard", "router", "stats_agent", "config_agent", "case_agent", "rca_agent", "clarify", "finalize"):
        assert n in m


async def test_rca_survives_failing_evidence_tool(world, monkeypatch):
    # 근거 도구 하나(SLA)가 실패해도 나머지 근거로 답하고, 실패는 단계 기록에 남음
    import uuid

    import app.api.routes.sla as sla

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(sla, "_compute_row", boom)
    res = await run_rca(12)
    assert res["route"] == "rca_agent" and res["outcome"] == "success"
    assert "조회 실패" in res["answer"]
    _, steps = _run(world, uuid.UUID(res["run_id"]))
    sla_step = next(s for s in steps if s.name == "get_sla_status")
    assert sla_step.error and "RuntimeError" in sla_step.error
