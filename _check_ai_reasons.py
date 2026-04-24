"""AI 실패 사유 세분화 검증 — kind 13종 + analysis_note.

시나리오:
  1) 정상 질문 → llm_error=None, analysis_note=None or no_match (질문에 따라)
  2) 빈 질문 ("ab") → analysis_note.kind='empty_question'
  3) 길이 충분하지만 매우 추상적 → analysis_note.kind='no_match' 가능
  4) 잘못된 키 (런타임) → llm_error.kind='auth_failed' (구 'http_error' 아님), title/detail/suggestion 모두 채워짐
  5) AI_PROVIDER=fallback (런타임) → llm_error.kind='not_configured' (성공 응답)
"""

from __future__ import annotations

import subprocess
import sys

import requests

BASE = "http://127.0.0.1:8000/api"


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin1234"}, timeout=10)
    return r.json()["access_token"]


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== AI 실패 사유 세분화 검증 ==")
    tok = login()

    def empty_question():
        r = requests.post(f"{BASE}/ai/ask", json={"question": "ab"}, headers=H(tok), timeout=30)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        note = body.get("analysis_note")
        assert note is not None, "analysis_note 누락"
        assert note["kind"] == "empty_question", f"기대 empty_question, got {note['kind']}"
        assert "5자" in note["detail"], f"detail 부적절: {note['detail']}"
        assert "권장" in note["suggestion"] or "예)" in note["suggestion"]
        print(f"     → {note['title']} | {note['detail'][:60]}")
    step("빈 질문 → analysis_note.empty_question", empty_question)

    def normal_question_no_note_or_match():
        r = requests.post(
            f"{BASE}/ai/ask",
            json={"question": "보험개발원 API 가 401 에러로 막혔어 어떻게 처리"},
            headers=H(tok), timeout=60,
        )
        body = r.json()
        # llm_error 가 있으면 새 구조 (title/detail/suggestion) 들어있어야 함
        if body.get("llm_error"):
            err = body["llm_error"]
            for f in ("kind", "title", "detail", "suggestion", "provider"):
                assert f in err, f"llm_error 에 {f} 누락"
            assert err["title"] and err["detail"] and err["suggestion"], "title/detail/suggestion 빈 문자열"
            print(f"     → llm_error: kind={err['kind']} title={err['title']}")
        else:
            print(f"     → 정상 LLM 응답 mode={body['mode']}")
    step("정상 질문 — llm_error 있다면 3단 구조 모두 존재", normal_question_no_note_or_match)

    def bad_key_returns_auth_failed():
        out = subprocess.run([
            "docker", "compose", "exec", "-T", "backend", "python", "-c",
            """
import asyncio
from app.core.config import get_settings, Settings
import app.core.config as cfg
import app.services.ai.llm as L
from app.services.ai.llm import chat, LLMError, LLMResponse

get_settings.cache_clear()
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
        print('TITLE=', res.title)
        print('DETAIL=', res.detail[:140])
        print('SUGGEST=', res.suggestion[:160])
        print('STATUS=', res.status)
    elif isinstance(res, LLMResponse):
        print('UNEXPECTED_SUCCESS')
asyncio.run(go())
"""
        ], capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
        out_text = (out.stdout or '') + (out.stderr or '')
        assert "KIND= auth_failed" in out_text, f"기대 auth_failed (구 http_error 아님): {out_text[:400]}"
        assert "TITLE= API 키 인증 실패" in out_text, f"title 한글 부적절: {out_text[:400]}"
        assert "STATUS= 401" in out_text, out_text[:400]
        assert "SUGGEST=" in out_text and ("새 키" in out_text or "교체" in out_text), (
            f"suggestion 부적절: {out_text[:400]}"
        )
        print(f"     → kind=auth_failed, status=401, title/detail/suggestion 모두 한글로 채워짐")
    step("잘못된 키 → kind='auth_failed' (3단 구조 + status=401)", bad_key_returns_auth_failed)

    def fallback_provider_not_configured():
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
        print('TITLE=', res.title)
        print('SUGGEST=', res.suggestion[:160])
asyncio.run(go())
"""
        ], capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
        out_text = (out.stdout or '') + (out.stderr or '')
        assert "KIND= not_configured" in out_text, out_text[:300]
        assert "TITLE= AI 분석기 비활성화" in out_text, out_text[:300]
        assert "AI_PROVIDER" in out_text, "suggestion 에 환경변수 명 누락"
    step("AI_PROVIDER=fallback → kind='not_configured' (3단 구조)", fallback_provider_not_configured)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All AI reason scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())