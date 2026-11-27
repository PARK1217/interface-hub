"""공통 LLM 어댑터 — 제공사 대체·오류 분류·토큰/비용 기록 (가짜 채팅 모델, 네트워크 없음)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult

from app.agents import llm as agent_llm
from app.agents import tracing
from app.agents.tracing import LLMCallTracker, Tracer
from app.core.config import Settings
from app.services.ai import llm as legacy
from app.services.ai.llm import LLMError, LLMResponse


class _Unauthorized(BaseChatModel):
    """항상 401 을 던지는 가짜 모델."""

    @property
    def _llm_type(self) -> str:
        return "fake-401"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        req = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
        raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req, text="bad key"))


def _fake_ok(text: str, tin: int, tout: int, model: str) -> GenericFakeChatModel:
    msg = AIMessage(content=text, usage_metadata={"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout},
                    response_metadata={"model_name": model})
    return GenericFakeChatModel(messages=iter([msg]))


@pytest.fixture
def two_providers(monkeypatch):
    s = Settings(ai_provider="mistral", mistral_api_key="bad", openai_api_key="good",
                 ai_fallback_chain="mistral,openai", usd_krw_rate=1400.0)
    monkeypatch.setattr(legacy, "get_settings", lambda: s)
    monkeypatch.setattr(tracing, "get_settings", lambda: s)
    tracing._price_table.cache_clear()

    def fake_build(provider: str, model: str):
        if provider == "mistral":
            return _Unauthorized()
        return _fake_ok("정상 답변", 120, 30, model)

    monkeypatch.setattr(agent_llm, "build_chat_model", fake_build)
    return s


async def test_fallback_records_attempt_and_tokens(two_providers):
    tracer = Tracer(entry="chat", user_id=1, request="test", enabled=False)
    tracker = LLMCallTracker(tracer, agent_id="stats_agent")
    res, attempts = await agent_llm.ainvoke_llm("stats_agent", "질문", system="sys", tracker=tracker)

    assert isinstance(res, LLMResponse)
    assert res.provider == "openai" and res.content == "정상 답변"
    # 첫 제공사 실패가 기존 llm_attempts 형식으로 남음
    assert attempts == [{"provider": "mistral", "kind": "auth_failed", "status": 401, "title": "API 키 인증 실패"}]
    llm_steps = [s for s in tracer.steps if s.kind == "llm"]
    assert len(llm_steps) == 2
    ok = llm_steps[1]
    assert (ok.input_tokens, ok.output_tokens) == (120, 30)  # 응답 usage 그대로
    # gpt-4o-mini: (120*0.15 + 30*0.60)/1e6 USD * 1400
    assert ok.cost_krw == Decimal(str(round((120 * 0.15 + 30 * 0.60) / 1_000_000 * 1400, 4)))
    assert llm_steps[0].error and "HTTPStatusError" in llm_steps[0].error


async def test_unknown_price_is_null(two_providers, monkeypatch):
    monkeypatch.setattr(agent_llm, "build_chat_model", lambda p, m: _fake_ok("답", 10, 5, "unpriced-model"))
    tracer = Tracer(entry="chat", user_id=None, request="q", enabled=False)
    res, _ = await agent_llm.ainvoke_llm("config_agent", "q", tracker=LLMCallTracker(tracer, agent_id="config_agent"))
    assert isinstance(res, LLMResponse)
    step = [s for s in tracer.steps if s.kind == "llm"][0]
    assert step.input_tokens == 10 and step.cost_krw is None  # 단가를 지어내지 않음


async def test_not_configured_when_no_keys(monkeypatch):
    s = Settings(ai_provider="fallback")
    monkeypatch.setattr(legacy, "get_settings", lambda: s)
    res, attempts = await agent_llm.ainvoke_llm("stats_agent", "q")
    assert isinstance(res, LLMError) and res.kind == "not_configured"
    assert attempts == []


async def test_empty_response_moves_to_next_provider(two_providers, monkeypatch):
    def fake_build(provider: str, model: str):
        return _fake_ok("" if provider == "mistral" else "두 번째", 5, 0 if provider == "mistral" else 3, model)

    monkeypatch.setattr(agent_llm, "build_chat_model", fake_build)
    res, attempts = await agent_llm.ainvoke_llm("case_agent", "q")
    assert isinstance(res, LLMResponse) and res.provider == "openai"
    assert attempts[0]["kind"] == "empty_response"


def test_level_model_spec(monkeypatch):
    s = Settings(ai_provider="mistral", mistral_api_key="k",
                 agent_model_fast="mistral:mistral-small-latest,openai:gpt-4o-mini", agent_model_top="big-model")
    monkeypatch.setattr(legacy, "get_settings", lambda: s)
    assert agent_llm.model_name_for("mistral", "fast") == "mistral-small-latest"
    assert agent_llm.model_name_for("anthropic", "top") == "big-model"
    # 수준 값이 비면 전환 전과 같은 기본 모델
    assert agent_llm.model_name_for("mistral", "balanced") == "mistral-large-latest"


def test_redact_masks_secrets():
    s = tracing.redact("문의 admin@example.com 010-1234-5678 Authorization: Bearer abc.def.ghi api_key=sk-deliberately-invalid-key-0000")
    assert "example.com" not in s and "1234-5678" not in s and "abc.def" not in s and "sk-deliberately" not in s
    assert len(tracing.redact("가" * 500)) <= tracing.SUMMARY_MAX + 1


async def test_snapshot_model_name_still_priced(two_providers, monkeypatch):
    # OpenAI 는 응답에 날짜 붙은 스냅샷 이름을 돌려줌 → 단가는 요청한 이름으로 찾고, 응답 model 은 설정 이름 그대로
    monkeypatch.setattr(agent_llm, "build_chat_model", lambda p, m: _fake_ok("답", 1000, 100, "gpt-4o-mini-2024-07-18"))
    s = Settings(ai_provider="openai", openai_api_key="k", usd_krw_rate=1400.0)
    monkeypatch.setattr(legacy, "get_settings", lambda: s)
    monkeypatch.setattr(tracing, "get_settings", lambda: s)
    tracer = Tracer(entry="chat", user_id=None, request="q", enabled=False)
    res, _ = await agent_llm.ainvoke_llm("stats_agent", "q", tracker=LLMCallTracker(tracer, agent_id="stats_agent"))
    assert isinstance(res, LLMResponse) and res.model == "gpt-4o-mini"
    step = [x for x in tracer.steps if x.kind == "llm"][0]
    assert step.model == "gpt-4o-mini-2024-07-18" and step.cost_krw is not None


def test_user_ref_is_keyed(monkeypatch):
    a = tracing.user_ref(1)
    monkeypatch.setattr(tracing, "get_settings", lambda: Settings(secret_key="another-key"))
    assert a != tracing.user_ref(1) and a.startswith("u_")
