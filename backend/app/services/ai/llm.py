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
    """LLM 호출 실패 사유. 사용자/프론트에 노출 가능한 형태로 정규화.

    kind:
      - "not_configured": AI_PROVIDER=fallback 또는 키 비어있음 (정상 fallback 경로)
      - "http_error":     프로바이더가 4xx/5xx 응답 (status, body_excerpt 포함)
      - "timeout":        httpx ReadTimeout 등 (60초 초과)
      - "network":        DNS/TLS/connect 실패
      - "parse_error":    응답 JSON 파싱 실패 (스키마 변경 의심)
      - "unknown":        분류 안 되는 예외
    """
    kind: str
    provider: str
    message: str
    status: int | None = None
    body_excerpt: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "provider": self.provider,
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


async def chat(prompt: str, *, system: str | None = None) -> LLMResponse | LLMError:
    """선택된 프로바이더로 chat completion.

    반환:
        LLMResponse: 성공
        LLMError:    실패 사유 구조화 (kind/status/message/body_excerpt)

    rag.py 가 LLMError 를 받으면 fallback 으로 전환하면서 사유를 응답에 동봉.
    """
    s = get_settings()
    provider = (s.ai_provider or "").lower()
    if provider in ("", "fallback"):
        return LLMError(
            kind="not_configured", provider="fallback",
            message="AI_PROVIDER 가 'fallback' 으로 설정되어 있습니다.",
        )
    key = _get_key(provider)
    if not key:
        log.warning("AI_PROVIDER=%s 인데 해당 키가 비어있음 → fallback", provider)
        return LLMError(
            kind="not_configured", provider=provider,
            message=f"{provider.upper()}_API_KEY 가 비어있습니다. backend/.env 확인 필요.",
        )
    model = s.ai_model or _DEFAULT_MODELS.get(provider)
    if not model:
        return LLMError(
            kind="not_configured", provider=provider,
            message=f"AI_MODEL 미지정 + {provider} 기본 모델 없음.",
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
                    message=f"알 수 없는 AI_PROVIDER={provider}",
                )
        return LLMResponse(provider=provider, model=model, content=content.strip())
    except httpx.HTTPStatusError as e:
        # 프로바이더가 4xx/5xx 응답 — 가장 흔한 케이스 (401 invalid key, 429 rate limit, 5xx outage)
        status = e.response.status_code
        body = _excerpt(e.response.text)
        msg = _classify_http_error(provider, status, body)
        log.warning("LLM HTTP %s (provider=%s): %s", status, provider, body)
        return LLMError(
            kind="http_error", provider=provider, status=status,
            message=msg, body_excerpt=body,
        )
    except httpx.TimeoutException:
        log.warning("LLM timeout (provider=%s, 60s 초과)", provider)
        return LLMError(
            kind="timeout", provider=provider,
            message=f"{provider} 응답이 60초 안에 오지 않았습니다.",
        )
    except httpx.HTTPError as e:
        # ConnectError, ReadError, RemoteProtocolError 등 네트워크 계열
        log.warning("LLM network error (provider=%s): %s", provider, e)
        return LLMError(
            kind="network", provider=provider,
            message=f"네트워크 오류: {type(e).__name__} — 인터넷 연결 / 프록시 / DNS 확인",
        )
    except (KeyError, ValueError, TypeError) as e:
        # JSON 파싱 / 응답 스키마 mismatch
        log.exception("LLM parse error (provider=%s): %s", provider, e)
        return LLMError(
            kind="parse_error", provider=provider,
            message=f"{provider} 응답 형식이 예상과 다릅니다 ({type(e).__name__}).",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("LLM unknown error (provider=%s)", provider)
        return LLMError(
            kind="unknown", provider=provider,
            message=f"{type(e).__name__}: {e}",
        )


def _classify_http_error(provider: str, status: int, body: str | None) -> str:
    """HTTP status 별로 사용자 친화적 메시지."""
    if status == 401:
        return f"{provider} API 키가 유효하지 않습니다 (401). backend/.env 의 API 키 확인."
    if status == 403:
        return f"{provider} API 권한 부족 (403). 키의 모델 접근 권한 확인."
    if status == 404:
        return f"{provider} 모델/엔드포인트를 찾을 수 없음 (404). AI_MODEL 값 확인."
    if status == 429:
        return f"{provider} 요청 한도(rate limit) 초과 (429). 잠시 후 재시도하거나 플랜 확인."
    if 500 <= status < 600:
        return f"{provider} 서버 오류 ({status}). 프로바이더 상태 페이지 확인 필요."
    return f"{provider} HTTP {status}"


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