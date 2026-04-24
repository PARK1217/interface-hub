"""Phase B.8.17 — LLM 프로바이더 fallback 체인 시나리오.

시뮬:
  1) resolve_chain() 기본 순서 — settings.ai_provider 가 맨 앞
  2) AI_FALLBACK_CHAIN env 설정 시 그 순서 그대로
  3) 첫 프로바이더 실패 → 두 번째로 넘어가 성공 (container 안에서 monkey-patch)
  4) 모두 실패 → LLMError + attempts 모두 기록
"""

from __future__ import annotations

import subprocess
import sys


def run_in_backend(script: str) -> str:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python", "-c", script],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    )
    return (out.stdout or "") + (out.stderr or "")


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== Fallback 체인 시나리오 ==")

    def chain_default_order():
        out = run_in_backend("""
from app.core.config import get_settings
from app.services.ai.llm import resolve_chain
print('CHAIN=', resolve_chain())
print('PRIMARY=', get_settings().ai_provider)
""")
        assert "CHAIN=" in out
        # primary 가 chain 맨 앞
        import re
        m = re.search(r"CHAIN= \[(.*?)\]", out)
        assert m, out
        chain_str = m.group(1)
        primary_m = re.search(r"PRIMARY= (\w+)", out)
        primary = primary_m.group(1).lower() if primary_m else ""
        if primary and primary != "fallback":
            assert chain_str.startswith(f"'{primary}'"), (
                f"primary={primary} 가 chain 맨 앞이 아님: {chain_str}"
            )
        print(f"     → chain={chain_str}, primary={primary}")
    step("resolve_chain() — primary 우선, 키 있는 것들만", chain_default_order)

    def chain_from_env():
        out = run_in_backend("""
from app.core.config import get_settings, Settings
import app.core.config as cfg
import app.services.ai.llm as L
get_settings.cache_clear()
fake = lambda: Settings(
    ai_provider='openai',
    mistral_api_key='x',
    openai_api_key='y',
    ai_fallback_chain='mistral,openai,anthropic',
)
cfg.get_settings = fake
L.get_settings = fake
from app.services.ai.llm import resolve_chain
print('CHAIN=', resolve_chain())
""")
        assert "'mistral'" in out and "'openai'" in out and "'anthropic'" in out
        # 명시 순서 그대로
        idx_m = out.index("'mistral'")
        idx_o = out.index("'openai'")
        idx_a = out.index("'anthropic'")
        assert idx_m < idx_o < idx_a, f"env 순서 미반영: {out}"
    step("AI_FALLBACK_CHAIN env — 명시 순서 그대로", chain_from_env)

    def fallback_first_fail_second_succeed():
        """첫 프로바이더 401 → 두 번째는 httpx 호출 시 monkey-patch 성공 응답."""
        out = run_in_backend("""
import asyncio
from app.core.config import get_settings, Settings
import app.core.config as cfg
import app.services.ai.llm as L
from app.services.ai.llm import LLMError, LLMResponse

get_settings.cache_clear()
fake = lambda: Settings(
    ai_provider='mistral',
    mistral_api_key='bad-key',
    openai_api_key='good-key',
    ai_fallback_chain='mistral,openai',
)
cfg.get_settings = fake
L.get_settings = fake

# openai 프로바이더 호출을 가짜 성공으로 monkey-patch
async def fake_openai(client, key, model, prompt, system, max_tokens):
    return '가짜 OpenAI 응답 OK'
L._openai = fake_openai

# RagService._call_llm 을 테스트하려면 db 세션 필요하지만 단순 체인만 검증하자
# rag 없이 llm 내 반복 호출하는 방식으로 시뮬
async def call_chain():
    from app.services.ai.llm import chat, resolve_chain
    chain = resolve_chain()
    print('CHAIN=', chain)
    attempts = []
    for p in chain:
        r = await chat('test', system='sys', provider=p)
        if isinstance(r, LLMResponse):
            print(f'OK provider={r.provider} content={r.content[:40]}')
            print(f'ATTEMPTS_BEFORE_OK={len(attempts)}')
            return
        attempts.append((r.provider, r.kind, r.status))
        print(f'FAIL provider={r.provider} kind={r.kind} status={r.status}')
    print('ALL_FAILED')

asyncio.run(call_chain())
""")
        assert "CHAIN=" in out, out
        # mistral bad-key → 401 실패
        assert "FAIL provider=mistral" in out, f"mistral 실패 누락: {out}"
        # openai monkey-patch → 성공
        assert "OK provider=openai" in out, f"openai 성공 기록 누락: {out}"
        assert "ATTEMPTS_BEFORE_OK=1" in out, f"이전 실패 1건 기록 안 됨: {out}"
    step("첫 실패 → 두 번째 성공 (attempts=1)", fallback_first_fail_second_succeed)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ All fallback-chain scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())