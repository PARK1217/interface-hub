"""비밀번호 정책 검증 (Phase B.2).

금감원 전자금융감독규정 시행세칙 권고에 맞춘 최소 정책:
- 최소 8자
- 영문 / 숫자 / 특수문자 중 3종 이상 조합 (require_complexity=True)
- 사용자명과 동일 금지
- 직전 비밀번호와 동일 금지 (current_hash 비교)

검증 실패 시 ValueError 를 발생시키며, 라우트에서 잡아 HTTP 400 으로 변환.
모든 메시지는 한국어 — 운영자/사용자 화면에 그대로 노출됨.
"""

from __future__ import annotations

import re

from .auth import verify_password
from .config import get_settings

# 특수문자 정의 — OWASP password special characters 기준 (공백 제외)
_SPECIAL_RE = re.compile(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")


class PasswordPolicyError(ValueError):
    """비밀번호 정책 위반. 라우트가 받아서 400 으로 변환."""


def validate_password(
    plain: str,
    *,
    username: str | None = None,
    current_hash: str | None = None,
) -> None:
    """정책 위반 시 PasswordPolicyError 를 발생시킨다.

    Args:
        plain: 평문 신규 비밀번호.
        username: 동일 금지 검사용 (없으면 스킵).
        current_hash: 직전 비밀번호 동일 금지 검사용 (없으면 스킵).
    """
    s = get_settings()

    if not plain:
        raise PasswordPolicyError("비밀번호를 입력하세요.")

    if len(plain) < s.password_min_length:
        raise PasswordPolicyError(f"비밀번호는 최소 {s.password_min_length}자 이상이어야 합니다.")

    if s.password_require_complexity:
        groups = sum([
            bool(_LETTER_RE.search(plain)),
            bool(_DIGIT_RE.search(plain)),
            bool(_SPECIAL_RE.search(plain)),
        ])
        if groups < 3:
            raise PasswordPolicyError(
                "비밀번호는 영문·숫자·특수문자를 모두 포함해야 합니다."
            )

    if username and plain.lower() == username.lower():
        raise PasswordPolicyError("비밀번호는 사용자명과 같을 수 없습니다.")

    if current_hash and verify_password(plain, current_hash):
        raise PasswordPolicyError("새 비밀번호는 직전 비밀번호와 같을 수 없습니다.")


def describe_policy() -> str:
    """프론트 hint/안내문에 쓸 수 있는 한 줄 정책 설명."""
    s = get_settings()
    parts = [f"최소 {s.password_min_length}자"]
    if s.password_require_complexity:
        parts.append("영문·숫자·특수문자 모두 포함")
    return " · ".join(parts)