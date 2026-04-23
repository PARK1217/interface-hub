"""LLM 프로바이더 추상화 — Mistral / Anthropic / HuggingFace / OpenAI 통합.

설계 메모:
- httpx 직접 호출 (각 프로바이더 SDK 의존성 없이 가벼움).
- AI_PROVIDER 환경변수로 선택. 키 없거나 'fallback' 이면 None 반환 →
  rag.ask_fallback (TF-IDF 템플릿) 으로 폴백.
- 응답 형태: 프로바이더 메타 (모델명·provider) + content (str).
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


@dataclass
class LLMResponse:
    provider: str
    model: str
    content: str


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


async def chat(prompt: str, *, system: str | None = None) -> LLMResponse | None:
    """선택된 프로바이더로 chat completion. 키 없거나 호출 실패 시 None.

    rag.py 가 None 받으면 fallback (TF-IDF 템플릿) 으로 자동 전환.
    """
    s = get_settings()
    provider = (s.ai_provider or "").lower()
    if provider in ("", "fallback"):
        return None
    key = _get_key(provider)
    if not key:
        log.warning("AI_PROVIDER=%s 인데 해당 키가 비어있음 → fallback", provider)
        return None
    model = s.ai_model or _DEFAULT_MODELS.get(provider)
    if not model:
        log.warning("provider=%s 의 기본 모델 미정", provider)
        return None
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
                log.warning("알 수 없는 AI_PROVIDER=%s", provider)
                return None
        return LLMResponse(provider=provider, model=model, content=content.strip())
    except Exception as e:  # noqa: BLE001
        log.exception("LLM 호출 실패 (provider=%s): %s", provider, e)
        return None


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