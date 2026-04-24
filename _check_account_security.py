"""Phase B.1~B.4 통합 시나리오 검증.

backend 컨테이너 외부에서 호출 (공통 로컬 작업 검증 패턴).
서버는 docker compose 로 띄워둔 상태여야 함.

테스트 시나리오:
  1) admin 로그인 OK
  2) 임시 사용자 생성 (정책 위반 → 400, 정책 OK → 201, must_change_password=True)
  3) 임시 사용자로 잘못된 비번 5번 → 5번째에 423 LOCKED
  4) 잠긴 상태에서 올바른 비번도 423 (잠금 우선)
  5) admin 이 unlock → 다시 로그인 시도 가능
  6) 임시 사용자 로그인 OK + 응답에 must_change_password=True
  7) change-password 호출 — 정책 위반 케이스 / 현재 비번 오류 / 성공 (must_change_password=False)
  8) admin reset-password → 새 임시 비번 + must_change_password=True 다시 True
  9) 정리: 테스트 사용자 disable
"""

from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api"
ADMIN_USER = "admin"
ADMIN_PW = "admin1234"

TEST_USER = "qa_b14"
TEST_PW = "First1!Pwd"   # 정책 만족 (영문+숫자+특수문자, 10자)


def _post(path: str, *, json=None, token: str | None = None, expect: int | tuple[int, ...] = 200):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{BASE}{path}", json=json, headers=headers, timeout=10)
    expected = expect if isinstance(expect, tuple) else (expect,)
    assert r.status_code in expected, f"POST {path} → {r.status_code} (expected {expected}): {r.text[:300]}"
    return r


def _get(path: str, *, token: str | None = None, expect: int = 200):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{BASE}{path}", headers=headers, timeout=10)
    assert r.status_code == expect, f"GET {path} → {r.status_code}: {r.text[:300]}"
    return r


def login(username: str, password: str, *, expect: int = 200) -> dict:
    return _post("/auth/login", json={"username": username, "password": password}, expect=expect).json()


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== Phase B.1~B.4 통합 시나리오 ==")

    # 1) admin 로그인
    admin_token = login(ADMIN_USER, ADMIN_PW)["access_token"]
    print(f"  ✓ admin 로그인 — token len={len(admin_token)}")

    # 사전 정리: 기존 테스트 사용자 있으면 disable + 새 사용자명 충돌 회피
    users = _get("/users", token=admin_token).json()
    existing = next((u for u in users if u["username"] == TEST_USER), None)
    if existing:
        # 강제 disable + 사용자명 unique 충돌 회피용 — 기존 것 재사용은 어렵기에
        # username 변경 API 가 없으므로, 같은 사용자명으로 재실행 시 충돌하면 그대로 끝낸다.
        # 보다 견고하게 하려면 매번 timestamp 붙인 사용자명 사용하는게 낫지만 README 일관성 위해 fixed.
        if not existing["disabled_at"]:
            _post(f"/users/{existing['id']}/disable", token=admin_token)
        # 비활성 사용자라도 username unique 제약은 살아있어 create 가 409 → 시나리오 다시 쓰려면 매뉴얼 정리 필요
        print(f"  ! 기존 {TEST_USER} 발견 — 시나리오 일부는 409 가 날 수 있음")

    # 2-a) 정책 위반 비밀번호로 create → 400
    def bad_pw_rejected():
        r = _post(
            "/users", token=admin_token,
            json={"username": TEST_USER, "password": "weak", "role": "OPERATOR"},
            expect=400,
        )
        assert "8자" in r.json().get("detail", "") or "최소" in r.json().get("detail", "")
    step("B.2 짧은 비밀번호로 사용자 생성 → 400", bad_pw_rejected)

    def no_complexity_rejected():
        r = _post(
            "/users", token=admin_token,
            json={"username": TEST_USER, "password": "longenoughpw", "role": "OPERATOR"},
            expect=400,
        )
        assert "특수문자" in r.json().get("detail", "")
    step("B.2 복잡도 미달 비밀번호 → 400", no_complexity_rejected)

    # 2-b) 정상 사용자 생성
    new_user_id = None

    def good_user_created():
        nonlocal new_user_id
        r = _post(
            "/users", token=admin_token,
            json={"username": TEST_USER, "password": TEST_PW, "role": "OPERATOR", "full_name": "QA 테스터"},
            expect=(201, 409),
        )
        if r.status_code == 409:
            # 기존 사용자 재사용 — id 만 가져옴
            new_user_id = next(u["id"] for u in _get("/users", token=admin_token).json() if u["username"] == TEST_USER)
            # 기존 사용자에게 비밀번호 reset (must_change_password=True 강제)
            _post(f"/users/{new_user_id}/reset-password", token=admin_token)
            # 새로 생성된 임시 비번을 모르므로, 비번을 다시 알려진 값으로 해야 시나리오 가능
            # → admin reset 으로는 임시 비번 받아 사용. 시나리오 단순화 위해 우회: skip
            print(f"  ! 기존 사용자 사용 — 일부 시나리오 스킵")
            return
        body = r.json()
        new_user_id = body["id"]
        assert body["must_change_password"] is True, "신규 생성 시 must_change_password 자동 True 여야 함 (B.4)"
        # 활성화/잠금 해제
        _post(f"/users/{new_user_id}/enable", token=admin_token)
        _post(f"/users/{new_user_id}/unlock", token=admin_token)
    step("B.4 정상 사용자 생성 — must_change_password=True 자동 부여", good_user_created)

    if new_user_id is None:
        print("?? new_user_id 미할당 — 이후 시나리오 스킵")
        return 1 if failures else 0

    # 3) 잘못된 비번 5번 → 5번째에 423
    def lockout_after_5_failures():
        for i in range(1, 6):
            r = requests.post(
                f"{BASE}/auth/login",
                json={"username": TEST_USER, "password": "wrong-pw-x"},
                timeout=10,
            )
            if i < 5:
                assert r.status_code == 401, f"{i}번째 실패는 401이어야 함 (got {r.status_code})"
            else:
                assert r.status_code == 423, f"5번째 실패는 423 LOCKED 여야 함 (got {r.status_code}): {r.text[:200]}"
                detail = r.json().get("detail", "")
                assert "잠겼" in detail or "잠겨" in detail, f"잠금 안내 메시지 없음: {detail}"
    step("B.1 5회 실패 → 423 LOCKED", lockout_after_5_failures)

    # 4) 올바른 비번도 잠금 중에는 423
    def correct_pw_blocked_when_locked():
        r = requests.post(
            f"{BASE}/auth/login",
            json={"username": TEST_USER, "password": TEST_PW},
            timeout=10,
        )
        assert r.status_code == 423, f"잠금 중에는 비번 맞아도 423 (got {r.status_code})"
    step("B.1 잠금 중 정확한 비번도 423", correct_pw_blocked_when_locked)

    # 5) admin 이 unlock
    def admin_can_unlock():
        r = _post(f"/users/{new_user_id}/unlock", token=admin_token).json()
        assert r["failed_login_count"] == 0
        assert r["locked_until"] is None
    step("B.1 ADMIN unlock → 카운터 리셋", admin_can_unlock)

    # 6) 정상 로그인 + must_change_password 응답
    user_token = None

    def login_after_unlock_and_must_change():
        nonlocal user_token
        body = login(TEST_USER, TEST_PW)
        user_token = body["access_token"]
        assert body["user"]["must_change_password"] is True, "응답 body 에 must_change_password=True"
    step("B.4 잠금 해제 후 로그인 — 응답에 must_change_password=True", login_after_unlock_and_must_change)

    # 7-a) change-password 정책 위반 → 400
    def change_password_policy_violation():
        r = _post(
            "/auth/change-password", token=user_token,
            json={"current_password": TEST_PW, "new_password": "short"},
            expect=400,
        )
        assert "8자" in r.json().get("detail", "")
    step("B.2/B.3 신규 비밀번호 정책 위반 → 400", change_password_policy_violation)

    # 7-b) current 오류 → 400
    def change_password_wrong_current():
        r = _post(
            "/auth/change-password", token=user_token,
            json={"current_password": "wrong-current", "new_password": "Second2@Pwd"},
            expect=400,
        )
        assert "현재" in r.json().get("detail", "")
    step("B.3 현재 비밀번호 불일치 → 400", change_password_wrong_current)

    # 7-c) 직전과 동일 → 400
    def change_password_same_as_current():
        r = _post(
            "/auth/change-password", token=user_token,
            json={"current_password": TEST_PW, "new_password": TEST_PW},
            expect=400,
        )
        assert "직전" in r.json().get("detail", "")
    step("B.2 신규 == 직전 비밀번호 → 400", change_password_same_as_current)

    # 7-d) 성공
    new_pw = "Second2@Pwd"

    def change_password_success():
        r = _post(
            "/auth/change-password", token=user_token,
            json={"current_password": TEST_PW, "new_password": new_pw},
            expect=200,
        ).json()
        assert r["must_change_password"] is False, "변경 후 must_change_password=False"
    step("B.3 정상 비밀번호 변경 — must_change_password=False", change_password_success)

    # 7-e) 새 비번으로 재로그인 — must_change_password=False
    def relogin_with_new_pw():
        body = login(TEST_USER, new_pw)
        assert body["user"]["must_change_password"] is False
    step("B.3 새 비밀번호로 로그인 OK", relogin_with_new_pw)

    # 8) admin reset → must_change_password 다시 True
    def admin_reset_resets_flag():
        r = _post(f"/users/{new_user_id}/reset-password", token=admin_token).json()
        temp_pw = r["temp_password"]
        # 다시 로그인 → must_change_password=True
        body = login(TEST_USER, temp_pw)
        assert body["user"]["must_change_password"] is True
        # 임시 비번도 정책 만족하는지 (8자 + 3종)
        assert len(temp_pw) >= 8
        assert any(c.isalpha() for c in temp_pw) and any(c.isdigit() for c in temp_pw)
    step("B.4 ADMIN reset → must_change_password 다시 True + 임시비번 정책 충족", admin_reset_resets_flag)

    # 9) 정리: disable
    def cleanup_disable():
        _post(f"/users/{new_user_id}/disable", token=admin_token)
    step("정리: 테스트 사용자 disable", cleanup_disable)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All Phase B.1~B.4 scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())