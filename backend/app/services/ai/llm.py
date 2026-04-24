"""LLM 프로바이더 추상화 — Mistral / Anthropic / HuggingFace / OpenAI 통합.

설계 메모:
- httpx 직접 호출 (각 프로바이더 SDK 의존성 없이 가벼움).
- AI_PROVIDER 환경변수로 선택. 키 없거나 'fallback' 이면 LLMError("not_configured") 반환 →
  rag.ask_fallback (TF-IDF 템플릿) 으로 폴백.
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
    "anthropic": "claude-3-5-sonnet-20240620",
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
    """선택된 프로바이더로 chat completion. 실패 시 LLMError (kind/title/detail/suggestion).

    provider 인자가 주어지면 settings.ai_provider 보다 우선 (fallback 체인 순회용).
    """
    s = get_settings()
    provider = (provider or s.ai_provider or "").lower()
    if provider in ("", "fallback"):
        return LLMError(
            kind="not_configured", provider="fallback",
            title="AI 분석기 비활성화",
            detail="AI_PROVIDER 가 'fallback' 으로 설정되어 LLM 호출을 건너뜁니다.",
            suggestion="LLM 분석을 쓰려면 backend/.env 에서 AI_PROVIDER 를 mistral/anthropic/openai/huggingface 중 하나로 바꾸고 해당 키를 채우세요.",
        )
    key = _get_key(provider)
    if not key:
        log.warning("AI_PROVIDER=%s 인데 해당 키가 비어있음 → fallback", provider)
        return LLMError(
            kind="not_configured", provider=provider,
            title="API 키 미설정",
            detail=f"AI_PROVIDER={provider} 인데 {provider.upper()}_API_KEY 환경변수가 비어있습니다.",
            suggestion=f"backend/.env 에 {provider.upper()}_API_KEY 를 설정한 뒤 컨테이너를 재기동하세요.",
        )
    model = s.ai_model or _DEFAULT_MODELS.get(provider)
    if not model:
        return LLMError(
            kind="not_configured", provider=provider,
            title="모델 미지정",
            detail=f"AI_MODEL 환경변수가 비어있고 {provider} 의 기본 모델이 정의되지 않았습니다.",
            suggestion=f"backend/.env 의 AI_MODEL 에 {provider} 가 지원하는 모델명을 직접 지정하세요.",
        )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            if provider == "mistral":
                content = await _mistral(client, key, model, prompt, system, s.ai_max_tokens)
            elif provider == "anthropic":
                content = await _anthropic(client, key, model, prompt, system, s.ai_max_tokens)
            elif provider == "huggingface":
                content = await _huggingface(client, key, model, prompt, system, s.ai_max_tokens)
            elif provider == "openai":
                content = await _openai(client, key, model, prompt, system, s.ai_max_tokens)
            else:
                return LLMError(
                    kind="not_configured", provider=provider,
                    title="알 수 없는 프로바이더",
                    detail=f"AI_PROVIDER={provider} 는 지원되지 않는 값입니다.",
                    suggestion="mistral / anthropic / openai / huggingface / fallback 중 하나로 설정하세요.",
                )
        # 빈 응답 검출 — HTTP 200 인데 모델이 빈 답을 준 경우 (드물지만 발생)
        cleaned = (content or "").strip()
        if not cleaned:
            return LLMError(
                kind="empty_response", provider=provider,
                title="모델이 빈 답변을 반환",
                detail=f"{provider}/{model} 호출은 성공했지만 응답 본문이 비어있습니다.",
                suggestion="질문을 더 구체적으로 다시 작성하거나, AI_MODEL 을 다른 모델로 바꿔 재시도하세요.",
            )
        return LLMResponse(provider=provider, model=model, content=cleaned)
    except httpx.HTTPStatusError as e:
        # 프로바이더가 4xx/5xx 응답 — 401 invalid key, 429 rate limit, 5xx outage
        status = e.response.status_code
        body = _excerpt(e.response.text)
        log.warning("LLM HTTP %s (provider=%s): %s", status, provider, body)
        kind, title, detail, suggestion = _classify_http_error(provider, status)
        return LLMError(
            kind=kind, provider=provider, status=status,
            title=title, detail=detail, suggestion=suggestion,
            body_excerpt=body,
        )
    except httpx.TimeoutException:
        log.warning("LLM timeout (provider=%s, 60s 초과)", provider)
        return LLMError(
            kind="timeout", provider=provider,
            title="응답 시간 초과",
            detail=f"{provider} API 가 60초 안에 응답을 보내지 않았습니다.",
            suggestion="잠시 후 다시 시도하거나, 더 가벼운 모델로 AI_MODEL 을 바꾸세요. 컨텍스트가 너무 길 수도 있습니다.",
        )
    except httpx.ConnectError:
        log.warning("LLM connect error (provider=%s)", provider)
        return LLMError(
            kind="network", provider=provider,
            title="API 서버 연결 불가",
            detail=f"{provider} API 호스트에 연결조차 되지 않았습니다 (DNS 또는 TCP 실패).",
            suggestion="인터넷 연결, 사내 방화벽/프록시 설정, DNS 확인. 평가관 PC 가 외부망 차단된 상태일 수 있습니다.",
        )
    except httpx.HTTPError as e:
        log.warning("LLM transport error (provider=%s): %s", provider, e)
        return LLMError(
            kind="network", provider=provider,
            title="네트워크 전송 오류",
            detail=f"{type(e).__name__}: {str(e)[:120]}",
            suggestion="네트워크가 불안정하거나 프로바이더 측 TLS 가 일시적으로 닫혔을 수 있습니다. 재시도하세요.",
        )
    except (KeyError, ValueError, TypeError) as e:
        log.exception("LLM parse error (provider=%s): %s", provider, e)
        return LLMError(
            kind="parse_error", provider=provider,
            title="응답 형식 오류",
            detail=f"{provider} 응답 JSON 이 예상한 스키마와 다릅니다 ({type(e).__name__}).",
            suggestion="프로바이더가 API 스펙을 변경했을 수 있습니다. backend/app/services/ai/llm.py 의 파서 확인 필요.",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("LLM unknown error (provider=%s)", provider)
        return LLMError(
            kind="unknown", provider=provider,
            title="분류되지 않은 오류",
            detail=f"{type(e).__name__}: {str(e)[:120]}",
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


# ---------- 프로바이더 구현 -------------------------------------------------

async def _mistral(client, key, model, prompt, system, max_tokens):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    r = await client.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": msgs, "max_tokens": max_tokens},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _anthropic(client, key, model, prompt, system, max_tokens):
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    r = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    r.raise_for_status()
    data = r.json()
    # Anthropic 응답 구조: content: [{type:"text", text:"..."}]
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts)


async def _huggingface(client, key, model, prompt, system, max_tokens):
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    r = await client.post(
        f"https://api-inference.huggingface.co/models/{model}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "inputs": full_prompt,
            "parameters": {"max_new_tokens": max_tokens, "return_full_text": False},
        },
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data:
        return data[0].get("generated_text", "")
    if isinstance(data, dict):
        return data.get("generated_text", str(data))
    return str(data)


async def _openai(client, key, model, prompt, system, max_tokens):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": msgs, "max_tokens": max_tokens},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]