"""추천 prompt 가 모두 분석 가능한지 검증 (Phase B.8.16).

시나리오:
  1) /ai/suggestions 호출 → limit 만큼 후보 받음
  2) 각 후보 prompt 를 /ai/ask 에 직접 돌림
  3) 응답이 no_match (outcome='no_match') 가 나오면 실패 — UI chip 클릭 시 분석 불가 chip 이 뜨는 상황
"""

from __future__ import annotations

import sys

import requests

BASE = "http://127.0.0.1:8000/api"


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin1234"}, timeout=10)
    return r.json()["access_token"]


def main() -> int:
    tok = login()
    H = {"Authorization": f"Bearer {tok}"}

    print("== 추천 prompt 가 모두 분석 가능한지 검증 ==")
    r = requests.get(f"{BASE}/ai/suggestions", headers=H, params={"limit": 10}, timeout=10)
    assert r.status_code == 200
    suggestions = r.json()
    print(f"  반환된 suggestions: {len(suggestions)}건")
    if not suggestions:
        print("  ! 후보 0 — 테스트 스킵")
        return 0

    fails: list[str] = []
    for i, s in enumerate(suggestions, 1):
        print(f"  [{i}/{len(suggestions)}] source={s['source']}: {s['text'][:60]}...")
        ask = requests.post(
            f"{BASE}/ai/ask", json={"question": s["text"]}, headers=H, timeout=90,
        )
        body = ask.json()
        outcome = body.get("outcome")
        if outcome == "no_match":
            fails.append(f"{s['source']}: {s['text'][:60]}")
            print(f"       ✗ no_match 가 발생 (분석 불가 chip)")
        elif outcome == "empty_question":
            fails.append(f"{s['source']}: {s['text'][:60]} (empty_question)")
            print(f"       ✗ empty_question — 추천 prompt 가 너무 짧음")
        else:
            print(f"       ✓ outcome={outcome}")

    print()
    if fails:
        print(f"❌ {len(fails)} 건의 추천 prompt 가 분석 불가로 이어짐:")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ 모든 추천 prompt 가 분석 가능 (no_match 0건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())