"""전역 알림 룰 검증.
evaluate_global_rule 의 핵심 4 케이스:
  1. severity 별 채널 화이트리스트 정확히 적용
  2. quiet hours 자정 안 넘김 (start < end)
  3. quiet hours 자정 넘김 (start > end)
  4. critical 은 quiet 무시 옵션 동작
  5. 주말 silence
"""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.models import AlertRule
from app.services.notifier import _within_quiet_hours, evaluate_global_rule


def main() -> int:
    failures: list[str] = []

    # 1) _within_quiet_hours — start < end 정상 범위
    cases_normal = [
        (8, 17, 7, False),   # 08~17 silence, 07시는 X
        (8, 17, 8, True),    # 08시 정확히
        (8, 17, 16, True),
        (8, 17, 17, False),  # 17시는 종료 시각이라 비활성
    ]
    for s, e, h, expected in cases_normal:
        got = _within_quiet_hours(s, e, h)
        if got != expected:
            failures.append(f"_within_quiet_hours({s},{e},{h}) expected={expected} got={got}")

    # 2) start > end (자정 넘김)
    cases_overnight = [
        (22, 8, 22, True),
        (22, 8, 23, True),
        (22, 8, 0, True),
        (22, 8, 7, True),
        (22, 8, 8, False),
        (22, 8, 12, False),
        (22, 8, 21, False),
    ]
    for s, e, h, expected in cases_overnight:
        got = _within_quiet_hours(s, e, h)
        if got != expected:
            failures.append(f"_within_quiet_hours overnight({s},{e},{h}) expected={expected} got={got}")

    # 3) start == end → 항상 False (의도: 0구간 silence)
    if _within_quiet_hours(10, 10, 10):
        failures.append("start==end 일 때 silence 안 되어야 함")

    # 4) evaluate_global_rule — severity 별 채널
    fake_rule = AlertRule(
        id=1,
        info_channels=["in_app"],
        warning_channels=["in_app", "slack"],
        critical_channels=["in_app", "slack", "email"],
        quiet_hours_enabled=False,
        quiet_hours_start=22, quiet_hours_end=8,
        quiet_hours_skip_critical=True,
        weekend_silence=False,
    )
    with patch("app.services.notifier._load_global_rule", return_value=fake_rule):
        d = evaluate_global_rule("info")
        if d.allowed_channels != {"in_app"}:
            failures.append(f"info severity 채널 틀림: {d.allowed_channels}")
        d = evaluate_global_rule("critical")
        if d.allowed_channels != {"in_app", "slack", "email"}:
            failures.append(f"critical severity 채널 틀림: {d.allowed_channels}")
        if d.quiet_now:
            failures.append("quiet_hours_enabled=False 인데 quiet_now=True")

    # 5) quiet hours 22~8 사이, 새벽 3시 시뮬레이션
    fake_rule.quiet_hours_enabled = True
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fake_now = datetime(2026, 4, 22, 3, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # 수요일 새벽 3시
    with patch("app.services.notifier._load_global_rule", return_value=fake_rule), \
         patch("app.services.notifier.now_kst", return_value=fake_now):
        # warning → quiet 적용
        d = evaluate_global_rule("warning")
        if not d.quiet_now:
            failures.append("새벽 3시 + quiet hours 22~8 인데 warning quiet_now=False")
        # critical → skip_critical=True 라 quiet 안 됨
        d = evaluate_global_rule("critical")
        if d.quiet_now:
            failures.append("critical 은 skip_critical=True 라 quiet 안 되어야 함")
        # skip_critical 끄면 critical 도 quiet
        fake_rule.quiet_hours_skip_critical = False
        d = evaluate_global_rule("critical")
        if not d.quiet_now:
            failures.append("skip_critical=False 면 critical 도 quiet 되어야 함")

    # 6) 주말 silence — 토요일
    fake_rule.quiet_hours_enabled = False
    fake_rule.weekend_silence = True
    fake_rule.quiet_hours_skip_critical = True
    sat = datetime(2026, 4, 25, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # 토요일 오후 2시
    with patch("app.services.notifier._load_global_rule", return_value=fake_rule), \
         patch("app.services.notifier.now_kst", return_value=sat):
        d = evaluate_global_rule("warning")
        if not d.quiet_now or "주말" not in (d.silenced_reason or ""):
            failures.append(f"토요일 + weekend_silence 인데 quiet 안 됨: {d}")
        # critical 은 skip
        d = evaluate_global_rule("critical")
        if d.quiet_now:
            failures.append("critical 은 weekend_silence + skip_critical=True 면 quiet 안 되어야 함")

    # 7) 룰 미설정 (None) → 기본 동작 (모든 채널 허용, silence 없음)
    with patch("app.services.notifier._load_global_rule", return_value=None):
        d = evaluate_global_rule("warning")
        if d.allowed_channels != {"in_app", "slack", "email"}:
            failures.append(f"룰 미설정 시 모든 채널 허용해야 함: {d.allowed_channels}")
        if d.quiet_now:
            failures.append("룰 미설정 시 quiet_now=False 여야 함")

    if failures:
        print("[FAIL] 전역 알림 룰 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] 전역 알림 룰 — 7개 시나리오 모두 통과")
    print("  * quiet hours 자정 안 넘김 / 자정 넘김 (22~8) / start==end 처리")
    print("  * severity 별 채널 라우팅 / critical skip 옵션 / 주말 silence / 룰 미설정 fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
