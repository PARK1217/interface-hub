"""AI 분석 실패 사유 표시 검증.

시나리오:
  1) 정상 LLM 호출 — mode=llm, llm_error=None
  2) 잘못된 API 키 (런타임 monkey-patch) — mode=fallback, llm_error.kind='http_error', status=401
  3) AI_PROVIDER=fallback (런타임) — mode=fallback, llm_error=None (정상 fallback)
"""

from __future__ import annotations

import sys

import requests

BASE = "http://127.0.0.1:8000/api"


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin1234"}, timeout=10)
    return r.json()["access_token"]


def ask(tok: str, q: str = "테스트 질문") -> dict:
    r = requests.post(
        f"{BASE}/ai/ask",
        json={"question": q},
        headers={"Authorization": f"Bearer {tok}"},
        timeout=60,
    )
    assert r.status_code == 200, f"ask 실패: {r.status_code} {r.text[:200]}"
    return r.json()


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== AI 실패 사유 노출 검증 ==")
    tok = login()

    def normal_llm_call():
        b = ask(tok, "보험개발원 API 응답 지연 원인 알려줘")
        # 키가 유효하면 mode=llm, 아니면 mode=fallback + llm_error 가 있어야 함
        assert b["mode"] in ("llm", "fallback")
        assert "llm_error" in b
        assert "answer" in b
        if b["mode"] == "llm":
            assert b["llm_error"] is None, f"성공인데 llm_error 가 있음: {b['llm_error']}"
            print(f"     → mode=llm provider={b['provider']} model={b['model']}")
        else:
            err = b["llm_error"]
            assert err is not None, "fallback 인데 llm_error 가 None"
            print(f"     → mode=fallback llm_error.kind={err['kind']} provider={err['provider']}")
            print(f"     → llm_error.message={err['message'][:120]}")
    step("정상 호출 — 응답 구조 검증 (mode + llm_error)", normal_llm_call)

    # 잘못된 키 시나리오 — 컨테이너 안에서 런타임으로 settings 캐시 우회 + 호출
    import subprocess

    def bad_key_returns_structured_error():
        out = subprocess.run([
            "docker", "compose", "exec", "-T", "backend", "python", "-c",
            """
import asyncio
from app.core.config import get_settings, Settings
from app.services.ai import llm as L
from app.services.ai.llm import chat, LLMError, LLMResponse

# settings 캐시 우회 — 직접 키 무효화
get_settings.cache_clear()
import app.core.config as cfg
import app.services.ai.llm as L
fake = lambda: Settings(
    ai_provider='mistral',
    ai_model='mistral-large-latest',
    mistral_api_key='sk-deliberately-invalid-key-for-test-12345',
)
cfg.get_settings = fake
L.get_settings = fake

async def go():
    res = await chat('hello', system='test')
    if isinstance(res, LLMError):
        print('KIND=', res.kind)
        print('PROV=', res.provider)
        print('STATUS=', res.status)
        print('MSG=', res.message[:140])
    elif isinstance(res, LLMResponse):
        print('UNEXPECTED_SUCCESS')
    else:
        print('UNKNOWN_TYPE=', type(res).__name__)

asyncio.run(go())
"""
        ], capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
        out_text = out.stdout + out.stderr
        assert "KIND= http_error" in out_text, f"http_error 분류 실패: {out_text[:400]}"
        assert "STATUS= 401" in out_text, f"401 status 미반영: {out_text[:400]}"
        assert "유효하지 않습니다" in out_text or "401" in out_text, f"메시지 부적절: {out_text[:400]}"
        print(f"     → 401 인식, message: {[l for l in out_text.split(chr(10)) if l.startswith('MSG=')][0][:120]}")
    step("잘못된 키 → LLMError(kind=http_error, status=401)", bad_key_returns_structured_error)

    def fallback_provider_returns_not_configured():
        out = subprocess.run([
            "docker", "compose", "exec", "-T", "backend", "python", "-c",
            """
import asyncio
from app.core.config import get_settings, Settings
import app.core.config as cfg
import app.services.ai.llm as L
get_settings.cache_clear()
fake = lambda: Settings(ai_provider='fallback')
cfg.get_settings = fake
L.get_settings = fake

from app.services.ai.llm import chat, LLMError

async def go():
    res = await chat('hi')
    if isinstance(res, LLMError):
        print('KIND=', res.kind)
        print('PROV=', res.provider)
asyncio.run(go())
"""
        ], capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
        out_text = out.stdout + out.stderr
        assert "KIND= not_configured" in out_text, f"not_configured 미반영: {out_text[:300]}"
        assert "PROV= fallback" in out_text, out_text[:300]
    step("AI_PROVIDER=fallback → LLMError(kind=not_configured)", fallback_provider_returns_not_configured)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All AI error scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())