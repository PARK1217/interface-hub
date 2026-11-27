"""공통 LLM 어댑터 — 프로젝트의 모든 LLM 호출은 여기를 지납니다.

- get_chat_model(agent_id): agents.yaml 의 model_level → .env AGENT_MODEL_{FAST|BALANCED|TOP} 로
  제공사별 모델을 정하고, 기존 제공사 순서(AI_PROVIDER / AI_FALLBACK_CHAIN)대로 LangChain 채팅
  모델을 만들어 `.with_fallbacks()` 로 잇습니다.
- ainvoke_llm(): 위 모델을 부르고, 기존 응답 형식(LLMResponse | LLMError + llm_attempts)으로 돌려줍니다.
  토큰·비용·걸린 시간은 콜백(app/agents/tracing.py)이 agent_steps 에 기록합니다.

설정은 `app.services.ai.llm.get_settings` 를 통해 읽습니다 (스모크 스크립트가 그 함수를 바꿔 끼워 테스트).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda

from app.agents.config import agent_config
from app.services.ai import llm as legacy
from app.services.ai.llm import EmptyResponseError, LLMError, LLMResponse, error_from_exception

log = logging.getLogger("noahub.agents.llm")

LLM_TIMEOUT_SECONDS = 60.0


# ---------- 모델 이름 정하기 ---------------------------------------------------

def _parse_level_spec(value: str) -> dict[str, str]:
    """"mistral:mistral-small-latest,openai:gpt-4o-mini" → {provider: model}. 이름만 있으면 {"*": name}."""
    out: dict[str, str] = {}
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            p, m = part.split(":", 1)
            out[p.strip().lower()] = m.strip()
        else:
            out["*"] = part
    return out


def model_name_for(provider: str, level: str | None) -> str | None:
    """수준별 모델 → 없으면 AI_MODEL → 제공사 기본 모델 (전환 전과 같은 순서)."""
    s = legacy.get_settings()
    spec = _parse_level_spec(getattr(s, f"agent_model_{level}", "") or "") if level else {}
    return spec.get(provider) or spec.get("*") or s.ai_model or legacy._DEFAULT_MODELS.get(provider)


# ---------- HuggingFace (기존 httpx 요청을 감싼 채팅 모델) ------------------------

class HuggingFaceInferenceChat(BaseChatModel):
    """HF Inference API 를 LangChain 채팅 모델로 감싼 것.

    새 의존성을 피하려고 기존 httpx 요청을 그대로 씁니다. 이 API 는 토큰 수를 주지 않으므로
    usage_metadata 는 비워 둡니다 (토큰을 어림하지 않음 → 기록은 NULL "토큰 미제공").
    """

    model: str
    api_key: str
    max_tokens: int = 2000
    timeout: float = LLM_TIMEOUT_SECONDS

    @property
    def _llm_type(self) -> str:
        return "huggingface-inference"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        return LangSmithParams(ls_provider="huggingface", ls_model_name=self.model, ls_model_type="chat",
                               ls_max_tokens=self.max_tokens)

    def _payload(self, messages: list[BaseMessage]) -> tuple[str, dict]:
        system = "\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
        user = "\n".join(str(m.content) for m in messages if not isinstance(m, SystemMessage))
        full_prompt = f"{system}\n\n{user}" if system else user
        return (
            f"https://api-inference.huggingface.co/models/{self.model}",
            {"inputs": full_prompt, "parameters": {"max_new_tokens": self.max_tokens, "return_full_text": False}},
        )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _parse(data: Any) -> ChatResult:
        if isinstance(data, list) and data:
            text = data[0].get("generated_text", "")
        elif isinstance(data, dict):
            text = data.get("generated_text", str(data))
        else:
            text = str(data)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        url, payload = self._payload(messages)
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            return self._parse(r.json())

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                         run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        url, payload = self._payload(messages)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            return self._parse(r.json())


# ---------- 제공사별 LangChain 채팅 모델 ------------------------------------------

def build_chat_model(provider: str, model: str) -> BaseChatModel:
    """제공사 1곳의 LangChain 채팅 모델. 재시도 0회·타임아웃 60초 (전환 전과 같음)."""
    s = legacy.get_settings()
    key = legacy._get_key(provider)
    common = {"max_retries": 0, "timeout": LLM_TIMEOUT_SECONDS, "max_tokens": s.ai_max_tokens}
    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(model=model, api_key=key, **common)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=key, **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=key, **common)
    if provider == "huggingface":
        return HuggingFaceInferenceChat(model=model, api_key=key or "", max_tokens=s.ai_max_tokens)
    raise ValueError(f"지원하지 않는 제공사: {provider}")


def message_text(msg: Any) -> str:
    text = getattr(msg, "text", None)
    if not isinstance(text, str) and callable(text):  # langchain-core 0.x 호환 (1.x 는 str 속성)
        text = text()
    if text is None:
        text = msg.content if isinstance(getattr(msg, "content", None), str) else ""
    return (text or "").strip()


def _ensure_text(msg: AIMessage) -> AIMessage:
    """빈 답이면 예외 → .with_fallbacks 가 다음 제공사로 넘어감 (전환 전 체인 동작과 같음)."""
    if not message_text(msg):
        raise EmptyResponseError("empty response")
    return msg


def _tagged(model: BaseChatModel, provider: str, model_name: str | None) -> Runnable:
    """콜백이 어느 제공사 호출인지 알 수 있게 metadata 를 붙임 (가짜 모델 테스트에서도 동작)."""
    return model.with_config(metadata={"agent_provider": provider, "agent_model": model_name})


@dataclass
class ChatPlan:
    """에이전트 1개의 LLM 호출 계획."""

    agent_id: str
    level: str | None
    chain: list[str]                                   # 시도할 제공사 순서 (resolve_chain)
    models: dict[str, str] = field(default_factory=dict)   # 호출 가능한 제공사 → 모델
    skipped: dict[str, LLMError] = field(default_factory=dict)  # 호출 전에 알 수 있는 설정 오류
    runnable: Runnable | None = None


def get_chat_model(agent_id: str) -> ChatPlan:
    """agent_id 에 맞는 채팅 모델 (제공사 대체 포함). 호출 가능한 제공사가 없으면 runnable=None."""
    level = agent_config(agent_id).get("model_level")
    plan = ChatPlan(agent_id=agent_id, level=level, chain=legacy.resolve_chain())
    runnables: list[Runnable] = []
    for p in plan.chain:
        err = legacy.not_configured_error(p)
        if err is not None:
            plan.skipped[p] = err
            continue
        model = model_name_for(p, level)
        try:
            runnables.append(_tagged(build_chat_model(p, model), p, model) | RunnableLambda(_ensure_text))
        except Exception as e:  # noqa: BLE001 — 잘못된 설정값 등
            plan.skipped[p] = error_from_exception(e, p, model)
            continue
        plan.models[p] = model
    if runnables:
        plan.runnable = runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]
    return plan


def _attempt(err: LLMError) -> dict:
    return {"provider": err.provider, "kind": err.kind, "status": err.status, "title": err.title}


async def ainvoke_llm(
    agent_id: str, prompt: str, *, system: str | None = None, tracker: Any = None,
) -> tuple[LLMResponse | LLMError, list[dict]]:
    """에이전트의 LLM 호출 1회 (제공사 대체 포함).

    반환: (최종 결과, 실패한 제공사 시도 목록) — 전환 전 RagService._call_llm 과 같은 형식.
    tracker: tracing.LLMCallTracker (토큰·비용 기록 + 제공사별 성공/실패 수집).
    """
    from app.agents.tracing import LLMCallTracker

    plan = get_chat_model(agent_id)
    if not plan.chain:
        return LLMError(
            kind="not_configured", provider="fallback",
            title="AI 분석기 비활성화",
            detail="설정된 프로바이더 키가 하나도 없습니다.",
            suggestion="backend/.env 에 MISTRAL_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY 중 하나 이상을 설정하세요.",
        ), []

    tracker = tracker or LLMCallTracker(None, agent_id=agent_id)
    final_exc: BaseException | None = None
    if plan.runnable is not None:
        messages: list[BaseMessage] = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        try:
            await plan.runnable.ainvoke(messages, config={"callbacks": [tracker], "run_name": f"{agent_id}.llm"})
        except Exception as e:  # noqa: BLE001 — 아래에서 제공사별로 분류
            final_exc = e

    attempts: list[dict] = []
    last_err: LLMError | None = None
    for p in plan.chain:
        if p in plan.skipped:
            last_err = plan.skipped[p]
            attempts.append(_attempt(last_err))
            continue
        if p not in plan.models:
            continue
        call = tracker.result_for(p)
        if call is None:  # 콜백이 오지 않음 — 호출 전에 실패했거나, 앞 제공사에서 이미 끝남
            later_ran = any(tracker.result_for(q) is not None for q in plan.chain[plan.chain.index(p) + 1:])
            if later_ran or (final_exc is not None and last_err is None):
                last_err = (error_from_exception(final_exc, p, plan.models[p]) if final_exc is not None and not later_ran
                            else LLMError(kind="unknown", provider=p, title="호출 전 실패",
                                          detail=f"{p} 모델 호출이 시작되지 못했습니다.",
                                          suggestion="모델 이름·설정값을 확인하세요."))
                attempts.append(_attempt(last_err))
            continue
        if call.ok:
            # 응답·ai_query_logs 의 model 은 전환 전처럼 설정한 이름 (스냅샷 이름은 agent_steps.model 에만)
            return LLMResponse(provider=p, model=plan.models[p], content=call.text), attempts
        last_err = error_from_exception(call.error, p, call.model or plan.models[p])
        attempts.append(_attempt(last_err))
    return last_err or LLMError(
        kind="unknown", provider="fallback",
        title="모든 프로바이더 실패", detail="체인 시도 결과 모두 실패했습니다.",
        suggestion="각 프로바이더 상태를 확인하세요.",
    ), attempts


async def chat_once(provider: str, prompt: str, *, system: str | None = None) -> LLMResponse | LLMError:
    """제공사 1곳만 호출 (대체 없음). services.ai.llm.chat() 의 구현."""
    from app.agents.tracing import LLMCallTracker

    err = legacy.not_configured_error(provider)
    if err is not None:
        return err
    s = legacy.get_settings()
    model = s.ai_model or legacy._DEFAULT_MODELS.get(provider)
    tracker = LLMCallTracker(None, agent_id=None)
    messages: list[BaseMessage] = [SystemMessage(content=system)] if system else []
    messages.append(HumanMessage(content=prompt))
    try:
        runnable = _tagged(build_chat_model(provider, model), provider, model) | RunnableLambda(_ensure_text)
        msg = await runnable.ainvoke(messages, config={"callbacks": [tracker]})
    except Exception as e:  # noqa: BLE001
        return error_from_exception(e, provider, model)
    return LLMResponse(provider=provider, model=model, content=message_text(msg))
