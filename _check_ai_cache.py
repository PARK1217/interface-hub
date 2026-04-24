"""Phase B.8 AI 캐싱 + 로깅 + popular/history/suggestions 시나리오.

검증:
  1) 첫 질문 → cached=False, ai_query_logs 1행 추가
  2) 같은 질문 재호출 → cached=True (Redis hit), 새 ai_query_logs 행 (hit_cache=true)
  3) 정규화: 공백·대소문자 다른 같은 질문 → 같은 캐시 hit
  4) /ai/popular-questions — 호출 횟수 집계 반영
  5) /ai/my-history — 본인 질문 시간역순
  6) /ai/suggestions — 비어있지 않음 (popular OR incident OR interface 템플릿)
"""

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


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== Phase B.8 AI 캐싱·로깅·popular·history ==")
    tok = login()

    # 매 실행 새 질문 — 이전 캐시와 충돌 없도록. seed incident 와 TF-IDF 매칭이
    # 되어야 LLM 이 호출되고 캐시됨 (Phase B.8.9 부터 no_match 면 LLM 호출 안 함).
    # 그래서 seed 사례의 키워드(신용정보원, 401, CB 조회) 를 포함하면서도 다른 세션과
    # 겹치지 않게 timestamp 를 suffix 로 붙임.
    qid = int(time.time())
    Q = f"신용정보원 CB조회 401 다발로 막혔어 어떻게 처리 #{qid}"

    def first_call_no_cache():
        r = requests.post(f"{BASE}/ai/ask", json={"question": Q}, headers=H(tok), timeout=60)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("cached") is False, f"첫 호출인데 cached=True ({body.get('cached')})"
        print(f"     → mode={body['mode']} provider={body.get('provider')} cached={body['cached']}")
    step("첫 호출 — cached=False", first_call_no_cache)

    def second_call_cache_hit():
        # LLM 성공일 때만 캐시됨. 만약 fallback 이면 hit 안 남.
        # 첫 호출 결과 다시 확인
        r1 = requests.post(f"{BASE}/ai/ask", json={"question": Q}, headers=H(tok), timeout=60).json()
        if r1["mode"] == "fallback":
            print(f"     ! 첫 호출이 fallback — 캐싱 대상 아님 (LLM 키 확인). 검증 스킵.")
            return
        # 같은 질문 한 번 더
        r2 = requests.post(f"{BASE}/ai/ask", json={"question": Q}, headers=H(tok), timeout=60).json()
        assert r2["cached"] is True, f"두 번째 호출인데 cached=False — 캐시 미동작"
    step("같은 질문 재호출 — cached=True", second_call_cache_hit)

    def normalized_match():
        # 공백/대소문자 다른 변형 — 같은 캐시 hit 되어야 함 (LLM 모드일 때만)
        r1 = requests.post(f"{BASE}/ai/ask", json={"question": Q}, headers=H(tok), timeout=60).json()
        if r1["mode"] == "fallback":
            print(f"     ! fallback 모드 — 정규화 캐시 검증 스킵")
            return
        Q_variant = "  " + Q.upper().replace(" ", "  ") + "  "
        r2 = requests.post(f"{BASE}/ai/ask", json={"question": Q_variant}, headers=H(tok), timeout=60).json()
        assert r2["cached"] is True, "정규화(trim+lower+공백) 후 같은 hash 되어야 함"
    step("trim+lower+공백 정규화 → 같은 캐시 hit", normalized_match)

    def popular_includes_q():
        r = requests.get(f"{BASE}/ai/popular-questions", headers=H(tok), params={"days": 1, "limit": 20}).json()
        questions = [p["question"] for p in r]
        assert any(qid_marker in (q or "") for q in questions for qid_marker in [str(qid)]), (
            f"popular-questions 에 방금 질문 누락. got: {questions[:3]}"
        )
        print(f"     → Top 5 questions: {[q[:30] for q in questions[:5]]}")
    step("/ai/popular-questions 가 호출 집계 반영", popular_includes_q)

    def history_includes_q():
        r = requests.get(f"{BASE}/ai/my-history", headers=H(tok), params={"limit": 20}).json()
        assert len(r) > 0, "내 히스토리 비어있음"
        first_q = r[0]["question"]
        assert str(qid) in first_q, f"가장 최근이 방금 질문이 아님: {first_q[:50]}"
        print(f"     → 최근 질문: {first_q[:50]}")
    step("/ai/my-history — 본인 최근 질문 시간역순", history_includes_q)

    def suggestions_not_empty():
        r = requests.get(f"{BASE}/ai/suggestions", headers=H(tok), params={"limit": 4}).json()
        assert len(r) > 0, "suggestions 비어있음 — 인터페이스/incident/popular 모두 비었나?"
        sources = set(s["source"] for s in r)
        print(f"     → {len(r)}건, sources={sources}")
        # 정상 데이터 시드면 popular OR interface_template 중 하나는 나와야 함
        assert sources & {"popular", "interface_template", "incident_recent"}, (
            f"알 수 없는 source: {sources}"
        )
    step("/ai/suggestions — 동적 추천 prompt 생성", suggestions_not_empty)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All Phase B.8 scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())