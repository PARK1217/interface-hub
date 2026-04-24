"""Phase B.9 — 외부 호출 재시도/backoff 검증.

executor._exec_with_retry 가 retry_max / retry_backoff_seconds 정책에 따라
재시도하는지, AUTH/FORMAT 같은 비재시도 대상은 즉시 종료하는지, attempt_count
가 정확히 기록되는지 확인.
"""
from __future__ import annotations

import asyncio
import time
import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.models.call_log import CallStatus
from app.models.interface import Interface, ProtocolType, AuthType
from app.services.executor import _exec_with_retry, _is_retryable, _classify


def make_itf(retry_max: int = 0, backoff: float = 0.05, timeout: float | None = None) -> Interface:
    """테스트용 minimum Interface (DB 미저장)."""
    return Interface(
        name="test",
        endpoint="http://example.test",
        method="GET",
        protocol=ProtocolType.REST,
        auth_type=AuthType.NONE,
        retry_max=retry_max,
        retry_backoff_seconds=backoff,
        timeout_seconds=timeout,
        enabled=True,
    )


async def main() -> int:
    failures: list[str] = []

    # 1) 항상 성공 → 1회만 호출, attempts=1
    async def runner_ok(_itf):
        return 200, {"ok": True}, None

    _, _, _, attempts = await _exec_with_retry(make_itf(retry_max=3), runner_ok)
    if attempts != 1:
        failures.append(f"성공 시 1회만 호출되어야 함, got attempts={attempts}")

    # 2) 5xx 무한 실패 → retry_max=2 면 총 3회 호출
    call_count = {"n": 0}
    async def runner_5xx(_itf):
        call_count["n"] += 1
        return 503, {"err": "down"}, None

    call_count["n"] = 0
    _, _, _, attempts = await _exec_with_retry(make_itf(retry_max=2), runner_5xx)
    if attempts != 3 or call_count["n"] != 3:
        failures.append(f"503 retry_max=2 면 3회 호출되어야 함, got attempts={attempts} count={call_count['n']}")

    # 3) AUTH 401 은 즉시 종료 (retry_max=3 이어도 1회만)
    call_count["n"] = 0
    async def runner_401(_itf):
        call_count["n"] += 1
        return 401, {"err": "unauth"}, None

    _, _, _, attempts = await _exec_with_retry(make_itf(retry_max=3), runner_401)
    if attempts != 1 or call_count["n"] != 1:
        failures.append(f"401 은 즉시 종료, got attempts={attempts} count={call_count['n']}")

    # 4) 처음 2번 실패 후 3번째 성공 — retry_max=3 이면 attempts=3 으로 종료
    call_count["n"] = 0
    async def runner_recovers(_itf):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return 500, {"err": "down"}, None
        return 200, {"ok": True}, None

    status, _, _, attempts = await _exec_with_retry(make_itf(retry_max=3), runner_recovers)
    if status != 200 or attempts != 3:
        failures.append(f"3번째 성공해야 종료. got status={status} attempts={attempts}")

    # 5) backoff 시간이 실제로 작동 — retry_max=2, backoff=0.1 → 0.1+0.2 = 0.3s 이상
    started = time.perf_counter()
    call_count["n"] = 0
    _, _, _, attempts = await _exec_with_retry(make_itf(retry_max=2, backoff=0.1), runner_5xx)
    elapsed = time.perf_counter() - started
    expected_min = 0.1 + 0.2  # 1차 backoff 0.1s, 2차 0.2s
    if elapsed < expected_min * 0.9:
        failures.append(f"backoff 너무 빠름. expected>={expected_min:.2f}s got={elapsed:.2f}s")

    # 6) _is_retryable 분류
    cases = [
        (CallStatus.TIMEOUT, True),
        (CallStatus.SERVER_ERROR, True),
        (CallStatus.FAILURE, True),
        (CallStatus.AUTH_ERROR, False),
        (CallStatus.FORMAT_ERROR, False),
        (CallStatus.SUCCESS, False),
    ]
    for st, expected in cases:
        got = _is_retryable(st, None)
        if got != expected:
            failures.append(f"_is_retryable({st}) expected={expected} got={got}")

    # 7) retry_max=0 (기본값) → 정확히 1회 호출
    call_count["n"] = 0
    _, _, _, attempts = await _exec_with_retry(make_itf(retry_max=0), runner_5xx)
    if attempts != 1 or call_count["n"] != 1:
        failures.append(f"retry_max=0 은 1회만 호출. got attempts={attempts} count={call_count['n']}")

    if failures:
        print("[FAIL] Phase B.9 재시도 정책 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] Phase B.9 재시도 정책 — 7개 시나리오 모두 통과")
    print("  * 성공 시 1회 / 503 retry_max=2 → 3회 / 401 즉시 종료 / 3번째 성공 / backoff 시간")
    print("  * _is_retryable 분류 (TIMEOUT/5xx/FAILURE → 재시도, AUTH/FORMAT → 즉시 종료)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
