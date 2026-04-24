"""Phase B.6 강제 로그아웃 / 토큰 무효화 시나리오.

전제: docker compose 로 백엔드 가동 중. admin 시드 계정 존재.

검증:
  1) admin 로그인 → 토큰 A 발급, /auth/me 성공
  2) admin 로그인 또 한 번 → 토큰 B 발급 (다른 탭 시뮬), /auth/me 성공
  3) 토큰 A 로 /auth/logout → 본인 모든 세션 무효화
  4) 토큰 A 로 /auth/me → 401 (당연)
  5) 토큰 B 로 /auth/me → 401 (다른 탭도 종료됨)
  6) admin 재로그인 → 새 토큰 C, /auth/me 성공
  7) operator 로 ADMIN 인 force-logout 시도 → 403
  8) admin 으로 operator force-logout → 200, operator 토큰 401
  9) 비밀번호 변경 → 응답에 새 토큰 포함, 새 토큰으로 /auth/me 성공, 옛 토큰은 401
"""

from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api"
ADMIN = ("admin", "admin1234")
OP = ("operator", "op1234")


def login(u: str, p: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": u, "password": p}, timeout=10)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def me(token: str, *, expect: int = 200) -> dict | None:
    r = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == expect, f"me {token[:8]}.. → {r.status_code}: {r.text[:200]}"
    return r.json() if r.status_code == 200 else None


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== Phase B.6 강제 로그아웃 ==")

    state: dict = {}

    def issue_two_tokens():
        state["tokenA"] = login(*ADMIN)
        # JWT iat 가 초 단위라 같은 초 내 발급 시 두 토큰이 동일할 수 있음 → 1초 슬립
        time.sleep(1.1)
        state["tokenB"] = login(*ADMIN)
        assert state["tokenA"] != state["tokenB"], "두 토큰이 동일 — 1초 간격 미흡"
        me(state["tokenA"])
        me(state["tokenB"])
    step("admin 두 세션 발급 — 둘 다 정상 동작", issue_two_tokens)

    def logout_invalidates_all_sessions():
        r = requests.post(f"{BASE}/auth/logout", headers={"Authorization": f"Bearer {state['tokenA']}"}, timeout=10)
        assert r.status_code == 200
        # 약간 대기 — tokens_invalid_before 갱신 + iat 비교 (초 정밀도) 안전
        time.sleep(1.1)
        me(state["tokenA"], expect=401)
        me(state["tokenB"], expect=401)
    step("/auth/logout → 본인 모든 활성 세션 401", logout_invalidates_all_sessions)

    def relogin_works():
        time.sleep(1.1)
        state["tokenC"] = login(*ADMIN)
        me(state["tokenC"])
    step("재로그인 후 새 토큰 정상 동작", relogin_works)

    def operator_cannot_force_logout():
        op_tok = login(*OP)
        r = requests.post(
            f"{BASE}/users/1/force-logout",
            headers={"Authorization": f"Bearer {op_tok}"},
            timeout=10,
        )
        assert r.status_code == 403, f"OPERATOR 가 force-logout → 403 기대 (got {r.status_code})"
        state["op_tok"] = op_tok
    step("OPERATOR 의 force-logout 호출 → 403", operator_cannot_force_logout)

    def admin_force_logout_operator():
        # operator user_id 조회
        r = requests.get(f"{BASE}/users", headers={"Authorization": f"Bearer {state['tokenC']}"}, timeout=10)
        assert r.status_code == 200
        op_id = next(u["id"] for u in r.json() if u["username"] == "operator")
        time.sleep(1.1)
        r2 = requests.post(
            f"{BASE}/users/{op_id}/force-logout",
            headers={"Authorization": f"Bearer {state['tokenC']}"},
            timeout=10,
        )
        assert r2.status_code == 200, r2.text[:200]
        time.sleep(1.1)
        # 기존 op 토큰은 무효
        me(state["op_tok"], expect=401)
    step("ADMIN force-logout → operator 토큰 401", admin_force_logout_operator)

    def change_password_returns_new_token():
        time.sleep(1.1)
        old = login(*OP)
        r = requests.post(
            f"{BASE}/auth/change-password",
            json={"current_password": "op1234", "new_password": "OpNew1@Pass"},
            headers={"Authorization": f"Bearer {old}"},
            timeout=10,
        )
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert "access_token" in body, "응답에 새 access_token 누락"
        assert body["access_token"] != old, "동일 토큰 재발급은 보안 문제"
        new_tok = body["access_token"]
        time.sleep(1.1)
        # 새 토큰은 OK, 옛 토큰은 401
        me(new_tok)
        me(old, expect=401)
        # 비밀번호 원복 — seed 와 동일한 op1234 로
        r2 = requests.post(
            f"{BASE}/auth/change-password",
            json={"current_password": "OpNew1@Pass", "new_password": "OpRestore1@"},
            headers={"Authorization": f"Bearer {new_tok}"},
            timeout=10,
        )
        assert r2.status_code == 200
        # 다시 op1234 로 (직전 비번 충돌 회피 위해 다른 비번 → op1234)
        time.sleep(1.1)
        latest_tok = r2.json()["access_token"]
        r3 = requests.post(
            f"{BASE}/auth/change-password",
            json={"current_password": "OpRestore1@", "new_password": "Op12!new"},
            headers={"Authorization": f"Bearer {latest_tok}"},
            timeout=10,
        )
        # 'op1234' 는 정책 위반 (특수문자 없음) — 시드 비번을 그대로 복원하려면 시드 재실행 필요.
        # 본 검증의 목적은 토큰 회전 동작 확인. 정책 위반 응답이면 OK.
        assert r3.status_code in (200, 400)
    step("비밀번호 변경 — 새 토큰 발급 + 옛 토큰 무효화", change_password_returns_new_token)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All Phase B.6 scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())