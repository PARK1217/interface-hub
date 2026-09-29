"""AI intent 분류 + 통계 컨텍스트 + no_match LLM 차단 검증."""
from __future__ import annotations

import sys

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

    print("== AI intent 분류 검증 ==")
    tok = login()

    def stats_question_classified():
        b = ask(tok, "지금 가장 장애가 잦은 인터페이스가 뭐야?")
        assert b["intent"] == "stats_query", f"기대 stats_query, got {b['intent']}"
        # 통계 분기는 similar_cases 비어있어야 함 (RAG 안 함)
        assert len(b["similar_cases"]) == 0, "stats_query 인데 similar_cases 가 있음"
        # 답변 본문에 인터페이스명/수치 인용이 들어가 있어야 (LLM 컨텍스트 표 기반)
        # 또는 fallback 모드면 통계 표가 그대로 들어있어야 함
        assert b["answer"], "answer 비어있음"
        print(f"     → intent=stats_query, mode={b['mode']}, answer 첫줄: {b['answer'].split(chr(10))[0][:80]}")
    step("'지금 가장 잦은 인터페이스' → stats_query 분기", stats_question_classified)

    def case_lookup_normal():
        b = ask(tok, "신용정보원 CB조회가 갑자기 401 다발로 막혔어 어떻게 처리?")
        assert b["intent"] == "case_lookup", f"기대 case_lookup, got {b['intent']}"
        # 정상 매칭이면 LLM 또는 fallback 답변 OK
        assert b["answer"]
        print(f"     → intent=case_lookup, mode={b['mode']}, similar_cases={len(b['similar_cases'])}건")
    step("'401 다발 어떻게 처리' → case_lookup", case_lookup_normal)

    def case_lookup_no_match_blocks_llm():
        # case_lookup 키워드("원인") 포함 + 보험사 인터페이스와 무관한 주제 → no_match 유도
        b = ask(tok, "강아지 산책 시간이 매번 달라지는 원인을 알려줘")
        assert b["intent"] == "case_lookup", f"기대 case_lookup, got {b['intent']}"
        note = b.get("analysis_note")
        if note and note["kind"] == "no_match":
            # 핵심: no_match면 mode=fallback (LLM 호출 안 함)
            assert b["mode"] == "fallback", f"no_match 인데 mode={b['mode']} (LLM 강행됨 — 환각 위험)"
            assert "관련된 과거 장애 사례를 찾지 못해" in b["answer"], (
                f"환각 차단 메시지 누락: {b['answer'][:120]}"
            )
            print(f"     → no_match → LLM 호출 차단 OK (mode=fallback)")
        else:
            print(f"     ! no_match 트리거 안 됨 — 키워드가 의외로 매칭됨. note={note}")
    step("case_lookup + no_match → LLM 강행 차단 (환각 방지)", case_lookup_no_match_blocks_llm)

    def config_question_classified():
        b = ask(tok, "현재 등록된 인터페이스 cron 스케줄 어떻게 설정되어 있어?")
        assert b["intent"] in ("config_query", "stats_query"), f"기대 config/stats, got {b['intent']}"
        assert b["answer"]
        print(f"     → intent={b['intent']}, mode={b['mode']}")
    step("'cron 스케줄 어떻게 설정' → config_query", config_question_classified)

    def general_question():
        # 어떤 카테고리 키워드도 없는 순수 문장
        b = ask(tok, "보험회사 기업문화 특징이 궁금합니다")
        assert b["intent"] == "general", f"기대 general, got {b['intent']}"
        print(f"     → intent=general, mode={b['mode']}")
    step("키워드 없는 질문 → general", general_question)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All AI intent scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())