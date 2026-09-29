"""AI 실패 재사용 + popular/history 필터 검증 (~B.8.14)."""
from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api"


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin1234"}, timeout=10)
    return r.json()["access_token"]


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def ask(tok: str, q: str) -> dict:
    r = requests.post(f"{BASE}/ai/ask", json={"question": q}, headers=H(tok), timeout=90)
    assert r.status_code == 200, r.text[:300]
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

    print("== AI 실패 재사용 + 필터 검증 ==")
    tok = login()
    qid = int(time.time())

    # 1) 너무 짧은 질문 (5자 미만) → outcome=empty_question
    # <too_short> 고정 hash 사용하므로 다른 실행과 공유됨 — 이건 의도된 동작
    short_q = "xy"  # 2자
    def short_question_outcome():
        b = ask(tok, short_q)
        assert b["outcome"] == "empty_question", f"기대 empty_question, got {b['outcome']}"
    step("너무 짧은 질문 → outcome=empty_question", short_question_outcome)

    def short_question_repeated_still_short():
        b = ask(tok, short_q)
        # 재호출도 동일 결과. (qhash 생성 전 단계 차단이라 실패 재사용 가드와 무관하게 empty_question)
        assert b["outcome"] == "empty_question"
    step("너무 짧은 질문 재호출 — 여전히 outcome=empty_question", short_question_repeated_still_short)

    # 2) no_match 질문 → 첫 호출 outcome=no_match, 두 번째 호출 repeated_failure=True
    no_match_q = f"강아지 산책 시간 원인 알려줘 {qid}"  # case_lookup 키워드 + 도메인 무관

    def no_match_first_call():
        b = ask(tok, no_match_q)
        assert b["outcome"] == "no_match", f"기대 no_match, got {b['outcome']}"
        assert b["repeated_failure"] is False
    step("no_match 첫 호출 → outcome=no_match", no_match_first_call)

    # no_match 는 사용자 입력 결함이라 DB 기록 안 함.
    # 재호출 시에도 repeated_failure=False (매번 동일 안내, LLM 호출은 여전히 차단)
    def no_match_not_persisted():
        b = ask(tok, no_match_q)
        assert b["outcome"] == "no_match"
        # 24h 가드는 환경 결함에만 적용 — 사용자 입력 결함은 매번 즉시 판정
        assert b["mode"] == "fallback", "no_match 면 LLM 호출은 어차피 차단"
    step("no_match 재호출 — DB 저장 없이 매번 즉시 안내", no_match_not_persisted)

    def no_match_not_in_history():
        # 이번 세션에서 empty_question/no_match 호출 4회 했는데
        # DB 에 새로 쌓이지 않았는지 확인 (qid 가 unique 하니 이번 실행 것만 필터 가능).
        # 기존 실행의 오래된 로그는 남아있어도 괜찮음 (새 정책은 신규 저장만 차단).
        r = requests.get(f"{BASE}/ai/my-history", headers=H(tok),
                        params={"limit": 50, "include_failed": True}).json()
        for h in r:
            assert no_match_q not in h["question"], (
                f"이번 실행의 no_match 질문이 DB 에 저장됨 (정책 미동작): {h['question'][:60]}"
            )
        print(f"     → 이번 세션 no_match 질문 DB 미저장 확인 OK (히스토리 {len(r)}건 중)")
    step("no_match 는 이번 실행에서 DB 에 안 남음", no_match_not_in_history)

    # 3) popular-questions — 실패 질문 (no_match / empty_question) 포함되지 않아야
    def popular_excludes_failures():
        r = requests.get(f"{BASE}/ai/popular-questions", headers=H(tok),
                        params={"days": 1, "limit": 50}).json()
        for p in r:
            # short_q 는 공통 hash 라 제외하고, no_match_q (qid 포함) 만 체크
            assert no_match_q not in p["question"], f"no_match 이 popular 에 포함됨: {p['question']}"
            assert p["question"].strip() != short_q, f"empty_question 이 popular 에 포함됨: {p['question']}"
        print(f"     → popular {len(r)}건 중 실패 질문 누락 확인 OK")
    step("popular-questions — 실패 질문 제외", popular_excludes_failures)

    # 4) my-history 기본은 성공만, include_failed=True 면 실패도 포함
    def history_default_excludes_failures():
        r = requests.get(f"{BASE}/ai/my-history", headers=H(tok), params={"limit": 50}).json()
        for h in r:
            assert h["outcome"] == "success", f"기본 히스토리에 실패 포함됨: {h['outcome']} · {h['question'][:40]}"
    step("my-history 기본 — 성공만", history_default_excludes_failures)

    def history_include_failed():
        # empty_question/no_match 는 DB 기록 안 함. 환경 결함(llm_failed
        # /no_history/scikit_missing) 만 include_failed 로 보임. 이번 세션에서
        # empty_question/no_match 는 생성됐지만 저장 안 됐어야 함.
        r = requests.get(f"{BASE}/ai/my-history", headers=H(tok),
                        params={"limit": 50, "include_failed": True}).json()
        outcomes = {h["outcome"] for h in r}
        # 사용자 결함은 DB 에 없어야 함 (새 정책)
        assert "empty_question" not in outcomes, (
            f"empty_question 이 DB 에 저장됨 — B.8.15 정책 미동작. outcomes={outcomes}"
        )
        # 이번 세션의 qid 포함 no_match 질문은 DB 에 없어야 함
        for h in r:
            assert no_match_q not in h["question"], (
                f"이번 no_match 질문이 DB 에 저장됨: {h['question'][:60]}"
            )
        print(f"     → include_failed=True outcomes={outcomes} (사용자 결함 제외 확인)")
    step("my-history include_failed=True — 환경 결함만 (사용자 결함 제외)", history_include_failed)

    # 5) suggestions popular 소스도 실패 질문 제외
    def suggestions_excludes_failures():
        r = requests.get(f"{BASE}/ai/suggestions", headers=H(tok),
                        params={"limit": 10}).json()
        for s in r:
            if s["source"] == "popular":
                assert short_q not in s["text"]
                assert no_match_q not in s["text"]
        print(f"     → suggestions {len(r)}건 중 popular 소스는 실패 질문 없음")
    step("suggestions — popular 소스 실패 제외", suggestions_excludes_failures)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All AI repeat-block scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())