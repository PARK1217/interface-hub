"""AI 응답 Redis 캐싱 + 질의 로깅 (Phase B.8).

설계:
- 캐시 키: SHA256(normalized_question + provider + model + top_k). 정규화는
  trim + 소문자 + 다중 공백→1개. "보험개발원 5xx?" 와 " 보험개발원 5xx? "
  는 같은 캐시 hit.
- 성공 응답만 (mode=='llm') 캐싱. fallback 은 캐시 안 함 — 키 복구/네트워크
  복구 후 다음 호출에서 LLM 다시 시도해야 함.
- TTL 기본 1시간 (settings.ai_cache_ttl). 비용/응답속도 최적화 + 인터페이스
  설정/키 갱신이 1시간 내 반영되도록 보수적으로 짧게.
- Redis 실패는 best-effort — 로그만 남기고 원래 흐름 진행.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from redis import asyncio as aioredis

from app.core.config import get_settings

log = logging.getLogger("noahub.ai.cache")

_KEY_PREFIX = "ai:ask:"
_WHITESPACE_RE = re.compile(r"\s+")

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def normalize_question(q: str) -> str:
    """trim + 소문자 + 공백 단일화. 캐시 / popular 그룹화 키 모두 동일 규칙."""
    return _WHITESPACE_RE.sub(" ", (q or "").strip().lower())


def question_hash(q: str, *, provider: str, model: str | None, top_k: int) -> str:
    """캐시 / 통계 그룹화 키. 동일 질문이라도 provider/model/top_k 다르면 별도."""
    norm = normalize_question(q)
    raw = f"{norm}|{provider}|{model or ''}|{top_k}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_cached(key_hash: str) -> dict | None:
    """캐시 hit 시 응답 dict 반환. miss / Redis 오류 시 None."""
    try:
        client = _get_client()
        raw = await client.get(_KEY_PREFIX + key_hash)
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("Redis get 실패 (cache 우회): %s", e)
        return None


async def set_cached(key_hash: str, payload: dict) -> None:
    """LLM 성공 응답 캐싱. fallback / LLMError 응답은 호출자가 스킵."""
    try:
        client = _get_client()
        ttl = get_settings().ai_cache_ttl_seconds
        await client.set(_KEY_PREFIX + key_hash, json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception as e:  # noqa: BLE001
        log.warning("Redis set 실패 (캐싱 스킵): %s", e)


async def invalidate_all() -> int:
    """관리자 캐시 비우기 — 모델/프로바이더 변경 후 사용. 삭제 건수 반환."""
    try:
        client = _get_client()
        deleted = 0
        async for k in client.scan_iter(match=_KEY_PREFIX + "*", count=200):
            await client.delete(k)
            deleted += 1
        return deleted
    except Exception as e:  # noqa: BLE001
        log.warning("Redis 캐시 비우기 실패: %s", e)
        return 0