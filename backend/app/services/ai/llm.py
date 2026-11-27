"""LLM 프로바이더 설정·오류 분류 — Mistral / Anthropic / HuggingFace / OpenAI.

설계 메모:
- 실제 호출은 LangChain 채팅 모델 어댑터(app/agents/llm.py)가 합니다. 이 모듈은
  제공사 순서(resolve_chain), 키 확인, 오류 분류(LLMError) 를 맡습니다.
- AI_PROVIDER 환경변수로 선택. 키 없거나 'fallback' 이면 LLMError("not_configured") 반환 →
  TF-IDF 템플릿 / 통계 표 그대로 답으로 폴백.
- 응답 형태:
    - 성공 → LLMResponse (provider/model/content)
    - 실패 → LLMError (kind/status/message/provider) — 호출자가 사용자에게 사유 노출 가능
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

log = logging.getLogger("noahub.ai.llm")

# 프로바이더별 기본 모델 (ai_model 미지정 시)
_DEFAULT_MODELS = {
    "mistral": "mistral-large-latest",
    "anthropic": "claude-sonnet-5-5",  # 기존 claude-3-5-sonnet-20240620 은 2025-10-28 종료
    "huggingface": "meta-llama/Llama-3-70b-instruct",
    "openai": "gpt-4o-mini",
}

# 응답 본문 일부만 사용자에게 노출 (전체는 너무 길고 secret 위험)
_BODY_EXCERPT_MAX = 240


@dataclass
class LLMResponse:
    provider: str
    model: str
    content: str


@dataclass
class LLMError:
    """LLM 호출 실패 사유. 사용자/프론트에 노출 가능한 3단 (title/detail/suggestion).

    kind 분류 — UI 가 색/아이콘 결정에 사용:
      [설정 오류 — 운영자가 backend/.env 또는 AI 페이지로 즉시 해결 가능]
      - "not_configured":    AI_PROVIDER='fallback' 또는 해당 프로바이더 키 비어있음
      - "auth_failed":       401 — API 키 무효/만료
      - "forbidden":         403 — 키는 유효하나 모델 접근 권한 없음
      - "model_not_found":   404 — AI_MODEL 이름 오타/단종된 모델

      [프로바이더 측 일시 장애 — 잠시 후 재시도]
      - "rate_limited":      429 — 한도 초과 (분당/일당)
      - "server_error":      500 — 프로바이더 내부 오류
      - "gateway_unreachable": 502/503/504 — 프로바이더 게이트웨이/일시 점검

      [네트워크 / 응답 이상]
      - "timeout":           60초 안에 응답 못 받음 (느린 모델/큰 컨텍스트)
      - "network":           DNS/TLS/connect 자체가 실패 (사내 방화벽/프록시/오프라인)
      - "empty_response":    HTTP 200 인데 본문이 비어있음
      - "parse_error":       응답 JSON 형식이 예상과 다름 (SDK/모델 버전 mismatch)

      [기타]
      - "unknown":           위 분류 어디에도 안 맞는 예외
    """
    kind: str
    provider: str
    title: str           # UI 헤더 — "API 키 인증 실패" 같은 한 줄
    detail: str          # 상세 사유 한 줄 — "Mistral 이 401 을 반환했습니다."
    suggestion: str      # 권장 조치 한 줄 — "backend/.env 의 MISTRAL_API_KEY 를 새 키로 교체하세요."
    status: int | None = None
    body_excerpt: str | None = None

    @property
    def message(self) -> str:
        """레거시 호환 — 기존 message 필드를 detail+suggestion 합쳐 노출."""
        return f"{self.detail} {self.suggestion}".strip()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "message": self.message,
            "status": self.status,
            "body_excerpt": self.body_excerpt,
        }


def is_configured() -> bool:
    """현재 설정된 프로바이더에 유효한 키가 있는지."""
    s = get_settings()
    p = (s.ai_provider or "").lower()
    if p in ("", "fallback"):
        return False
    return bool(_get_key(p))


def current_provider() -> str:
    """현재 활성 프로바이더명. 키 없으면 'fallback'."""
    return get_settings().ai_provider.lower() if is_configured() else "fallback"


def current_model() -> str | None:
    s = get_settings()
    p = s.ai_provider.lower()
    if p in ("", "fallback") or not _get_key(p):
        return None
    return s.ai_model or _DEFAULT_MODELS.get(p)


def _get_key(provider: str) -> str | None:
    s = get_settings()
    return {
        "mistral": s.mistral_api_key,
        "anthropic": s.anthropic_api_key,
        "huggingface": s.huggingface_api_key,
        "openai": s.openai_api_key,
    }.get(provider)


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    s = text.strip().replace("\n", " ")
    return s if len(s) <= _BODY_EXCERPT_MAX else s[: _BODY_EXCERPT_MAX] + "…"


def resolve_chain() -> list[str]:
    """시도할 프로바이더 순서.

    1. settings.ai_fallback_chain (쉼표 구분) 이 있으면 그대로 사용.
    2. 없으면 settings.ai_provider 를 맨 앞에 두고, 키가 설정된 나머지 프로바이더를
       mistral → anthropic → openai → huggingface 순으로 자동 추가.
    """
    s = get_settings()
    primary = (s.ai_provider or "").lower()
    chain_env = (s.ai_fallback_chain or "").strip()
    if chain_env:
        return [p.strip().lower() for p in chain_env.split(",") if p.strip()]
    default_order = ("mistral", "anthropic", "openai", "huggingface")
    configured = [p for p in default_order if _get_key(p)]
    if primary in configured:
        return [primary] + [p for p in configured if p != primary]
    return configured


async def chat(
    prompt: str, *, system: str | None = None, provider: str | None = None,
) -> LLMResponse | LLMError:
    """제공사 1곳으로 chat completion (대체 체인 없음). 실패 시 LLMError.

    에이전트 전환 후에는 LangChain 채팅 모델(app/agents/llm.py)로 호출합니다.
    provider 인자가 주어지면 settings.ai_provider 보다 우선 (스모크 스크립트 호환).
    """
    from app.agents.llm import chat_once

    s = get_settings()
    provider = (provider or s.ai_provider or "").lower()
    return await chat_once(provider, prompt, system=system)


def not_configured_error(provider: str) -> LLMError | None:
    """호출 전에 알 수 있는 설정 오류. 없으면 None."""
    s = get_settings()
    if provider in ("", "fallback"):
        return LLMError(
            kind="not_configured", provider="fallback",
            title="AI 분석기 비활성화",
            detail="AI_PROVIDER 가 'fallback' 으로 설정되어 LLM 호출을 건너뜁니다.",
            suggestion="LLM 분석을 쓰려면 backend/.env 에서 AI_PROVIDER 를 mistral/anthropic/openai/huggingface 중 하나로 바꾸고 해당 키를 채우세요.",
        )
    if provider not in _DEFAULT_MODELS:
        return LLMError(
            kind="not_configured", provider=provider,
            title="알 수 없는 프로바이더",
            detail=f"AI_PROVIDER={provider} 는 지원되지 않는 값입니다.",
            suggestion="mistral / anthropic / openai / huggingface / fallback 중 하나로 설정하세요.",
        )
    if not _get_key(provider):
        log.warning("AI_PROVIDER=%s 인데 해당 키가 비어있음 → fallback", provider)
        return LLMError(
            kind="not_configured", provider=provider,
            title="API 키 미설정",
            detail=f"AI_PROVIDER={provider} 인데 {provider.upper()}_API_KEY 환경변수가 비어있습니다.",
            suggestion=f"backend/.env 에 {provider.upper()}_API_KEY 를 설정한 뒤 컨테이너를 재기동하세요.",
        )
    if not (s.ai_model or _DEFAULT_MODELS.get(provider)):
        return LLMError(
            kind="not_configured", provider=provider,
            title="모델 미지정",
            detail=f"AI_MODEL 환경변수가 비어있고 {provider} 의 기본 모델이 정의되지 않았습니다.",
            suggestion=f"backend/.env 의 AI_MODEL 에 {provider} 가 지원하는 모델명을 직접 지정하세요.",
        )
    return None


class EmptyResponseError(Exception):
    """HTTP 200 인데 모델이 빈 답을 준 경우 — 대체 체인의 다음 제공사로 넘기기 위한 예외."""


def empty_response_error(provider: str, model: str | None) -> LLMError:
    return LLMError(
        kind="empty_response", provider=provider,
        title="모델이 빈 답변을 반환",
        detail=f"{provider}/{model} 호출은 성공했지만 응답 본문이 비어있습니다.",
        suggestion="질문을 더 구체적으로 다시 작성하거나, AI_MODEL 을 다른 모델로 바꿔 재시도하세요.",
    )


def _status_of(exc: BaseException) -> int | None:
    for obj in (exc, getattr(exc, "response", None)):
        code = getattr(obj, "status_code", None) if obj is not None else None
        if isinstance(code, int):
            return code
    return None


def _body_of(exc: BaseException) -> str | None:
    resp = getattr(exc, "response", None)
    try:
        text = resp.text if resp is not None else None
    except Exception:  # noqa: BLE001 — 스트리밍 응답 등
        text = None
    if not text:
        body = getattr(exc, "body", None)
        text = str(body) if body else None
    return _excerpt(text)


def error_from_exception(exc: BaseException, provider: str, model: str | None = None) -> LLMError:
    """LangChain 제공사 SDK 예외 → 기존 LLMError (kind/title/detail/suggestion).

    openai / anthropic SDK 는 status_code 속성, mistral 은 httpx.HTTPStatusError 를 던짐.
    """
    if isinstance(exc, EmptyResponseError):
        return empty_response_error(provider, model)
    status = _status_of(exc)
    name = type(exc).__name__
    if status is not None:
        body = _body_of(exc)
        log.warning("LLM HTTP %s (provider=%s): %s", status, provider, body)
        kind, title, detail, suggestion = _classify_http_error(provider, status)
        return LLMError(
            kind=kind, provider=provider, status=status,
            title=title, detail=detail, suggestion=suggestion, body_excerpt=body,
        )
    if isinstance(exc, httpx.TimeoutException) or "Timeout" in name:
        log.warning("LLM timeout (provider=%s, 60s 초과)", provider)
        return LLMError(
            kind="timeout", provider=provider,
            title="응답 시간 초과",
            detail=f"{provider} API 가 60초 안에 응답을 보내지 않았습니다.",
            suggestion="잠시 후 다시 시도하거나, 더 가벼운 모델로 AI_MODEL 을 바꾸세요. 컨텍스트가 너무 길 수도 있습니다.",
        )
    if isinstance(exc, httpx.ConnectError) or "Connection" in name or "Connect" in name:
        log.warning("LLM connect error (provider=%s)", provider)
        return LLMError(
            kind="network", provider=provider,
            title="API 서버 연결 불가",
            detail=f"{provider} API 호스트에 연결조차 되지 않았습니다 (DNS 또는 TCP 실패).",
            suggestion="인터넷 연결, 사내 방화벽/프록시 설정, DNS 확인. 평가관 PC 가 외부망 차단된 상태일 수 있습니다.",
        )
    if isinstance(exc, httpx.HTTPError):
        log.warning("LLM transport error (provider=%s): %s", provider, exc)
        return LLMError(
            kind="network", provider=provider,
            title="네트워크 전송 오류",
            detail=f"{name}: {str(exc)[:120]}",
            suggestion="네트워크가 불안정하거나 프로바이더 측 TLS 가 일시적으로 닫혔을 수 있습니다. 재시도하세요.",
        )
    if isinstance(exc, KeyError | ValueError | TypeError):
        log.warning("LLM parse error (provider=%s): %s", provider, exc)
        return LLMError(
            kind="parse_error", provider=provider,
            title="응답 형식 오류",
            detail=f"{provider} 응답 JSON 이 예상한 스키마와 다릅니다 ({name}).",
            suggestion="프로바이더가 API 스펙을 변경했을 수 있습니다. backend/app/agents/llm.py 의 제공사 설정 확인 필요.",
        )
    log.warning("LLM unknown error (provider=%s): %s", provider, name)
    return LLMError(
        kind="unknown", provider=provider,
        title="분류되지 않은 오류",
        detail=f"{name}: {str(exc)[:120]}",
        suggestion="backend 컨테이너 로그 (docker compose logs backend) 에서 전체 스택 트레이스를 확인하세요.",
    )


def _classify_http_error(provider: str, status: int) -> tuple[str, str, str, str]:
    """HTTP status → (kind, title, detail, suggestion)."""
    if status == 401:
        return (
            "auth_failed",
            "API 키 인증 실패",
            f"{provider} 가 API 키를 유효하지 않다고 거부했습니다 (HTTP 401).",
            f"키가 만료/회수됐을 수 있습니다. {provider} 콘솔에서 새 키를 발급해 backend/.env 의 {provider.upper()}_API_KEY 를 교체하세요.",
        )
    if status == 403:
        return (
            "forbidden",
            "모델 접근 권한 부족",
            f"{provider} 키는 유효하지만 현재 모델에 접근할 권한이 없습니다 (HTTP 403).",
            f"{provider} 콘솔에서 해당 키의 모델/티어 권한을 확인하거나, 권한이 있는 모델로 AI_MODEL 을 변경하세요.",
        )
    if status == 404:
        return (
            "model_not_found",
            "모델을 찾을 수 없음",
            f"{provider} 가 요청한 AI_MODEL 을 찾지 못했습니다 (HTTP 404). 오타이거나 단종된 모델일 수 있습니다.",
            f"backend/.env 의 AI_MODEL 값을 {provider} 가 현재 제공하는 모델명으로 교체하세요.",
        )
    if status == 429:
        return (
            "rate_limited",
            "요청 한도 초과",
            f"{provider} 가 분당/일당 요청 한도를 초과했다고 응답했습니다 (HTTP 429).",
            "1~2분 후 다시 시도하세요. 자주 발생하면 결제 플랜 업그레이드 또는 캐시 TTL 연장을 고려하세요.",
        )
    if status in (502, 503, 504):
        return (
            "gateway_unreachable",
            "프로바이더 일시 장애",
            f"{provider} 게이트웨이가 응답하지 않습니다 (HTTP {status}).",
            f"{provider} 상태 페이지를 확인하고, 보통 수 분 내 복구되니 잠시 후 재시도하세요.",
        )
    if 500 <= status < 600:
        return (
            "server_error",
            "프로바이더 내부 오류",
            f"{provider} 서버에서 내부 오류가 발생했습니다 (HTTP {status}).",
            f"{provider} 상태 페이지 확인 후 재시도. 계속되면 다른 프로바이더로 AI_PROVIDER 를 임시 전환하세요.",
        )
    return (
        "http_error",
        f"HTTP {status} 오류",
        f"{provider} 가 HTTP {status} 를 반환했습니다.",
        "응답 본문을 확인하고 프로바이더 문서를 참고하세요.",
    )
