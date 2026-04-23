from collections.abc import Generator
from typing import Sequence

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import decode_access_token
from app.core.database import get_db
from app.models import User, UserRole

__all__ = ["get_db", "DbSession", "get_current_user", "require_role", "get_optional_user"]


def DbSession() -> Generator[Session, None, None]:  # noqa: N802 — FastAPI Depends-friendly alias
    yield from get_db()


def _user_from_authorization(
    authorization: str | None, db: Session
) -> User | None:
    """Authorization: Bearer <jwt> 헤더에서 User 객체 복원. 실패 시 None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if not user or user.disabled_at is not None:
        return None
    return user


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """인증 필수 의존성. 토큰 없으면 401."""
    user = _user_from_authorization(authorization, db)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "로그인이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """인증 선택. 익명/비인증도 통과 (감사 로그용 actor 추출 등)."""
    return _user_from_authorization(authorization, db)


def require_role(allowed: Sequence[UserRole]):
    """역할 가드 데코레이터 팩토리.

    예시:
        @router.post("/...", dependencies=[Depends(require_role([UserRole.ADMIN]))])
    """
    allowed_set = set(allowed)

    def _checker(current: User = Depends(get_current_user)) -> User:
        if current.role not in allowed_set:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"권한 부족: {[r.value for r in allowed_set]} 필요 (현재 {current.role.value})",
            )
        return current

    return _checker