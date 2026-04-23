from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import create_access_token, verify_password
from app.core.time import now_kst
from app.models import User
from app.schemas.user import LoginRequest, LoginResponse, UserOut
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """로그인 — 성공 시 JWT 발급, 실패는 감사 로그에만 남기고 401."""
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        # 감사 — 누가 어디서 시도했는지 기록 (actor 는 None 익명)
        record_audit(
            db,
            actor=None,
            action="auth.login_failed",
            resource_type="user",
            resource_id=payload.username,
            request=request,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자명 또는 비밀번호가 올바르지 않습니다.")
    if user.disabled_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "비활성화된 계정입니다.")

    token, exp = create_access_token(user_id=user.id, username=user.username, role=user.role.value)
    user.last_login_at = now_kst()
    db.commit()
    db.refresh(user)
    record_audit(db, actor=user, action="auth.login", request=request)
    return LoginResponse(access_token=token, expires_at=exp, user=UserOut.model_validate(user))


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, str]:
    """로그아웃 — JWT 는 stateless 라 서버 측 무효화 없음. 감사 로그만 기록.

    실제 로그아웃은 클라이언트가 토큰 삭제. Phase B 에서 토큰 블랙리스트 추가 예정.
    """
    record_audit(db, actor=current, action="auth.logout", request=request)
    return {"detail": "logged out"}


@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current)