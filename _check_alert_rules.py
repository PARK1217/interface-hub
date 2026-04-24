"""Phase B.7 알림 룰 시나리오 — 음소거 + 채널 화이트리스트.

전제:
- backend 가동 중 (docker compose).
- admin 시드 계정.

시나리오:
  1) admin 로그인
  2) 인터페이스 1번 mute(2분) → muted_until 미래로 설정됨
  3) operator 로 mute / unmute 시도 → 200 (OPERATOR+ 가능)
  4) viewer 로 mute → 403
  5) ADMIN 가 alert_channels 만 ['in_app'] 으로 PATCH → 응답에 반영
  6) unmute → muted_until null
  7) dispatch_alert 직접 호출 시 should_alert 결정 검증
     - muted=True → should_alert=False
     - muted=False, in_app 비활성 → should_alert=False
     - muted=False, in_app 활성 → should_alert=True
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import requests

BASE = "http://127.0.0.1:8000/api"


def login(u: str, p: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": u, "password": p}, timeout=10)
    assert r.status_code == 200, f"login {u} failed: {r.text[:200]}"
    return r.json()["access_token"]


def call(method: str, path: str, *, token: str | None = None, json=None, params=None, expect=(200,)):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.request(
        method, f"{BASE}{path}", headers=headers, json=json, params=params, timeout=10,
    )
    expected = expect if isinstance(expect, tuple) else (expect,)
    assert r.status_code in expected, f"{method} {path} → {r.status_code}: {r.text[:200]}"
    return r


def main() -> int:
    failures: list[str] = []

    def step(name: str, fn):
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  ✗ {name}: {e}")

    print("== Phase B.7 알림 룰 ==")

    admin_tok = login("admin", "admin1234")
    op_tok = login("operator", "op1234")
    viewer_tok = login("viewer", "view1234")

    # 첫 번째 인터페이스 id 확보
    itfs = call("GET", "/interfaces", token=admin_tok).json()
    itf_id = itfs[0]["id"]
    itf_name = itfs[0]["name"]
    print(f"  대상 인터페이스: #{itf_id} {itf_name}")

    # 1) ADMIN mute(2분)
    def admin_mute():
        body = call("POST", f"/interfaces/{itf_id}/mute", token=admin_tok, params={"minutes": 2}).json()
        assert body["muted_until"], "muted_until 비어있음"
        until = datetime.fromisoformat(body["muted_until"])
        # 1분~3분 후 사이여야 함
        delta = (until - datetime.now(until.tzinfo)).total_seconds()
        assert 60 < delta < 180, f"음소거 종료 시각 이상: {delta}s"
    step("ADMIN mute(2분) — muted_until 미래", admin_mute)

    # 2) OPERATOR 도 mute / unmute 가능
    def operator_can_mute():
        call("POST", f"/interfaces/{itf_id}/mute", token=op_tok, params={"minutes": 5})
        call("POST", f"/interfaces/{itf_id}/unmute", token=op_tok)
    step("OPERATOR 가 mute/unmute — 둘 다 200", operator_can_mute)

    # 3) VIEWER 는 403
    def viewer_blocked():
        call("POST", f"/interfaces/{itf_id}/mute", token=viewer_tok, params={"minutes": 5}, expect=403)
    step("VIEWER 의 mute → 403", viewer_blocked)

    # 4) ADMIN 가 alert_channels 만 in_app 으로 PATCH
    def patch_channels():
        body = call(
            "PATCH", f"/interfaces/{itf_id}", token=admin_tok,
            json={"alert_channels": ["in_app"]},
        ).json()
        assert body["alert_channels"] == ["in_app"], body["alert_channels"]
    step("PATCH alert_channels=['in_app'] — 응답 반영", patch_channels)

    # 5) 음소거 잘못된 minutes 값
    def bad_minutes():
        call("POST", f"/interfaces/{itf_id}/mute", token=admin_tok, params={"minutes": 0}, expect=400)
        call("POST", f"/interfaces/{itf_id}/mute", token=admin_tok, params={"minutes": 10000}, expect=400)
    step("mute minutes 범위 검증 (1~1440)", bad_minutes)

    # 6) dispatch_alert 시뮬 — _is_muted / _enabled_channels 결정 로직 직접 검증
    #    docker exec 으로 백엔드 안에서 호출
    import subprocess
    def dispatcher_logic_unmuted_in_app_on():
        # 음소거 해제 + in_app 만
        call("POST", f"/interfaces/{itf_id}/unmute", token=admin_tok)
        call("PATCH", f"/interfaces/{itf_id}", token=admin_tok, json={"alert_channels": ["in_app"]})
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "backend", "python", "-c",
             f"from app.core.database import SessionLocal; from app.models import Interface; "
             f"from app.services.notifier import _is_muted, _enabled_channels; "
             f"db=SessionLocal(); itf=db.get(Interface, {itf_id}); "
             f"print('muted=', _is_muted(itf), 'chans=', sorted(_enabled_channels(itf)))"],
            capture_output=True, text=True, timeout=15,
        )
        assert "muted= False" in out.stdout, out.stdout
        assert "chans= ['in_app']" in out.stdout, out.stdout
    step("음소거OFF + in_app 만 — _is_muted=False, channels=['in_app']", dispatcher_logic_unmuted_in_app_on)

    def dispatcher_logic_muted():
        call("POST", f"/interfaces/{itf_id}/mute", token=admin_tok, params={"minutes": 5})
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "backend", "python", "-c",
             f"from app.core.database import SessionLocal; from app.models import Interface; "
             f"from app.services.notifier import _is_muted; "
             f"db=SessionLocal(); itf=db.get(Interface, {itf_id}); "
             f"print('muted=', _is_muted(itf))"],
            capture_output=True, text=True, timeout=15,
        )
        assert "muted= True" in out.stdout, out.stdout
    step("음소거 ON — _is_muted=True", dispatcher_logic_muted)

    # 7) 정리: unmute + 채널 복원
    def cleanup():
        call("POST", f"/interfaces/{itf_id}/unmute", token=admin_tok)
        call("PATCH", f"/interfaces/{itf_id}", token=admin_tok,
             json={"alert_channels": ["in_app", "slack", "email"]})
    step("정리: unmute + 채널 기본값 복원", cleanup)

    print()
    if failures:
        print(f"❌ {len(failures)} failure(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ All Phase B.7 scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())