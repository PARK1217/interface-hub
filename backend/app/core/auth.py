"""인증 유틸 — bcrypt 비밀번호 해싱 + PyJWT 토큰 발급/검증.

설계 메모:
- 비밀번호는 무조건 bcrypt 해시 후 저장. 평문 저장 절대 금지.
- JWT TTL 4시간 (settings.jwt_ttl_minutes). 만료 시 클라이언트가 재로그인.
- 리프레시 토큰은 Phase B 로 미룸 (지금은 단순 만료 후 재로그인).
- jwt_secret 미설정 시 SECRET_KEY 재사용 — 개발 편의성.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import bcrypt
import jwt

from .config import get_settings
from .time import KST

log = logging.getLogger("noahub.auth")


def _signing_key() -> str:
    s = get_settings()
    return s.jwt_secret or s.secret_key


def hash_password(plain: str) -> str:
    """bcrypt 해시. cost=12 (~250ms) — 보험사 표준 안전선."""
    if not plain:
        raise ValueError("password must be non-empty")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def create_access_token(
    *, user_id: int, username: str, role: str, session_version: int
) -> tuple[str, datetime]:
    """JWT 발급. 반환: (token, expires_at).

    payload: {sub, username, role, sv, iat, exp}
    sv (session_version) 는 강제 로그아웃 / 비밀번호 변경 시 사용자별 카운터를
    +1 시켜 기존 토큰을 일괄 무효화하기 위한 키.
    """
    s = get_settings()
    now = datetime.now(KST)
    exp = now + timedelta(minutes=s.jwt_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "sv": session_version,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _signing_key(), algorithm=s.jwt_algorithm)
    return token, exp


def decode_access_token(token: str) -> dict | None:
    """JWT 검증 + payload 반환. 만료/위변조 시 None."""
    if not token:
        return None
    s = get_settings()
    try:
        return jwt.decode(token, _signing_key(), algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        log.info("token expired")
        return None
    except jwt.InvalidTokenError as e:
        log.warning("invalid token: %s", e)
        return None
