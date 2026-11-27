"""안내 에이전트(라우터) 분기 — 규칙 먼저, 애매할 때만 LLM."""

from __future__ import annotations

import pytest

from app.agents import router
from app.core.config import Settings
from app.services.ai.llm import LLMError, LLMResponse


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(router, "is_configured", lambda: False)


@pytest.mark.parametrize("question, agent, intent", [
    ("지금 가장 장애가 잦은 인터페이스가 뭐야?", "stats_agent", "stats_query"),
    ("보험개발원 인터페이스 엔드포인트 url 이 뭐야?", "config_agent", "config_query"),
    ("신용정보원 CB조회가 갑자기 401 다발로 막혔어 어떻게 처리?", "case_agent", "case_lookup"),
    ("보험회사 기업문화 특징이 궁금합니다", "case_agent", "general"),  # 키워드 없음 → 전환 전처럼 사례 검색
])
async def test_rule_routes(no_llm, question, agent, intent):
    d = await router.decide(question, incident_id=None, tracer=None, parent=None)
    assert (d.agent_id, d.intent, d.method) == (agent, intent, "rule")


async def test_incident_goes_to_rca(no_llm):
    d = await router.decide("무엇이든", incident_id=7, tracer=None, parent=None)
    assert (d.agent_id, d.intent, d.method) == ("rca_agent", "root_cause", "incident")


def test_ambiguity_definition():
    assert router.is_ambiguous("보험회사 기업문화 특징이 궁금합니다")          # 점수 0
    assert not router.is_ambiguous("지금 가장 장애가 잦은 인터페이스가 뭐야?")  # stats 가 확실히 높음


@pytest.fixture
def llm_says(monkeypatch):
    calls: list[str] = []

    def _set(answer):
        async def fake(agent_id, prompt, *, system=None, tracker=None):
            calls.append(agent_id)
            if isinstance(answer, LLMError):
                return answer, []
            return LLMResponse(provider="fake", model="fake", content=answer), []

        monkeypatch.setattr(router, "is_configured", lambda: True)
        monkeypatch.setattr(router, "ainvoke_llm", fake)
        # 라우터 LLM 분류는 기본 꺼짐 → 이 픽스처에서만 켬
        monkeypatch.setattr(router, "get_settings", lambda: Settings(router_llm_fallback=True))
        return calls

    return _set


async def test_ambiguous_uses_llm(llm_says):
    calls = llm_says("root_cause")
    d = await router.decide("보험개발원 상태 좀 봐줘", incident_id=None, tracer=None, parent=None)
    assert calls == ["router"]
    assert (d.agent_id, d.intent, d.rule_intent, d.method) == ("rca_agent", "root_cause", "general", "llm")


async def test_clear_question_skips_llm(llm_says):
    calls = llm_says("root_cause")
    d = await router.decide("최근 7일 실패율 Top 5 인터페이스 알려줘", incident_id=None, tracer=None, parent=None)
    assert calls == [] and d.agent_id == "stats_agent"


@pytest.mark.parametrize("answer", ["모르겠어요", "stats_query 또는 case_lookup",
                                    LLMError(kind="timeout", provider="x", title="t", detail="d", suggestion="s")])
async def test_bad_llm_answer_falls_back_to_rule(llm_says, answer):
    llm_says(answer)
    d = await router.decide("보험회사 기업문화 특징이 궁금합니다", incident_id=None, tracer=None, parent=None)
    assert (d.agent_id, d.method) == ("case_agent", "rule(llm 실패)")


async def test_router_llm_fallback_off(llm_says, monkeypatch):
    calls = llm_says("root_cause")
    monkeypatch.setattr(router, "get_settings", lambda: Settings(router_llm_fallback=False))
    d = await router.decide("보험개발원 상태 좀 봐줘", incident_id=None, tracer=None, parent=None)
    assert calls == [] and d.method == "rule"


async def test_llm_routing_is_off_by_default(monkeypatch):
    # 기본 설정(ROUTER_LLM_FALLBACK 미설정)에서는 LLM 이 설정돼 있어도 분류에 쓰지 않음 = 전환 전과 같은 동작
    calls: list[str] = []

    async def fake(agent_id, prompt, *, system=None, tracker=None):
        calls.append(agent_id)
        return LLMResponse(provider="fake", model="fake", content="root_cause"), []

    monkeypatch.setattr(router, "is_configured", lambda: True)
    monkeypatch.setattr(router, "ainvoke_llm", fake)
    monkeypatch.setattr(router, "get_settings", lambda: Settings())
    d = await router.decide("보험개발원 상태 좀 봐줘", incident_id=None, tracer=None, parent=None)
    assert calls == [] and (d.agent_id, d.method) == ("case_agent", "rule")


async def test_router_llm_timeout_falls_back_to_rule(monkeypatch):
    import asyncio

    async def slow(agent_id, prompt, *, system=None, tracker=None):
        await asyncio.sleep(5)
        return LLMResponse(provider="fake", model="fake", content="root_cause"), []

    monkeypatch.setattr(router, "is_configured", lambda: True)
    monkeypatch.setattr(router, "ainvoke_llm", slow)
    monkeypatch.setattr(router, "get_settings",
                        lambda: Settings(router_llm_fallback=True, router_llm_timeout_seconds=0.05))
    d = await router.decide("보험개발원 상태 좀 봐줘", incident_id=None, tracer=None, parent=None)
    assert (d.agent_id, d.method) == ("case_agent", "rule(llm 시간 초과)")
