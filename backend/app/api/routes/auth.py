from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import create_access_token, hash_password, verify_password
from app.core.config import get_settings
from app.core.password_policy import PasswordPolicyError, validate_password
from app.core.time import now_kst
from app.models import User
from app.schemas.user import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    UserOut,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


# HTTP 423 LOCKED — 비표준이지만 WebDAV 에서 정의된 표준 상태 코드.
# 401 (인증 실패) 와 구분되어야 프론트가 별도 안내문을 띄울 수 있음.
HTTP_423_LOCKED = 423


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """로그인 — 성공 시 JWT 발급. 실패는 카운트 + 감사 로그.: 연속 실패가 settings.lockout_threshold 회 이상이면 잠금.
    잠금 중에는 비밀번호가 맞아도 423 LOCKED 로 거부 (남은 시간 안내).
    """
    s = get_settings()
    user = db.scalar(select(User).where(User.username == payload.username))

    # case 1) 사용자 자체가 없음 — 기존 사용자 존재 여부 누설 방지를 위해 401 통일
    if not user:
        record_audit(
            db, actor=None, action="auth.login_failed",
            resource_type="user", resource_id=payload.username,
            after={"reason": "no_such_user"},
            request=request,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자명 또는 비밀번호가 올바르지 않습니다.")

    # case 2) 비활성 계정
    if user.disabled_at is not None:
        record_audit(
            db, actor=None, action="auth.login_failed",
            resource_type="user", resource_id=user.username,
            after={"reason": "disabled"},
            request=request,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "비활성화된 계정입니다.")

    # case 3) 잠금 중 — 비밀번호 검증 전에 차단 (timing 노출은 허용 — 운영자가 빨리 알아야 함)
    now = now_kst()
    if user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        record_audit(
            db, actor=None, action="auth.login_blocked",
            resource_type="user", resource_id=user.username,
            after={"reason": "locked", "remaining_minutes": remaining},
            request=request,
        )
        raise HTTPException(
            HTTP_423_LOCKED,
            f"계정이 잠겨 있습니다. 약 {remaining}분 후 다시 시도하거나 관리자에게 문의하세요.",
        )

    # case 4) 비밀번호 검증
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        locked_now = False
        if s.lockout_threshold > 0 and user.failed_login_count >= s.lockout_threshold:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            locked_now = True
        db.commit()
        record_audit(
            db, actor=None, action="auth.login_failed",
            resource_type="user", resource_id=user.username,
            after={
                "reason": "wrong_password",
                "failed_count": user.failed_login_count,
                "locked": locked_now,
            },
            request=request,
        )
        if locked_now:
            # 잠금 발동 사실을 별도 감사 이벤트로도 남김 (조회 편의)
            record_audit(
                db, actor=None, action="auth.locked",
                resource_type="user", resource_id=user.username,
                after={
                    "lockout_minutes": s.lockout_minutes,
                    "threshold": s.lockout_threshold,
                },
                request=request,
            )
            raise HTTPException(
                HTTP_423_LOCKED,
                f"비밀번호 {s.lockout_threshold}회 연속 오류로 계정이 {s.lockout_minutes}분간 잠겼습니다.",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자명 또는 비밀번호가 올바르지 않습니다.")

    # case 5) 성공 — 카운터 리셋 + 마지막 로그인 시각 갱신
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    db.refresh(user)

    token, exp = create_access_token(
        user_id=user.id, username=user.username, role=user.role.value,
        session_version=user.session_version,
    )
    record_audit(db, actor=user, action="auth.login", request=request)
    return LoginResponse(access_token=token, expires_at=exp, user=UserOut.model_validate(user))


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, str]:
    """로그아웃 — 부터 서버 측에서도 토큰 무효화.

    session_version 을 +1 → 발급된 모든 JWT 의 sv 가 mismatch 되어 다음 요청부터
    401. 다른 탭/디바이스의 세션도 함께 종료.
    """
    current.session_version = (current.session_version or 1) + 1
    db.commit()
    record_audit(db, actor=current, action="auth.logout", request=request)
    return {"detail": "logged out"}


@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current)


@router.post("/change-password", response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PasswordChangeResponse:
    """본인 비밀번호 변경.

    - 현재 비밀번호 확인 후 진행
    - 신규 비밀번호는 정책 검증 (8자 + 3종 + 사용자명·직전과 다름)
    - 성공 시 must_change_password 플래그 해제
    """
    if not verify_password(payload.current_password, current.password_hash):
        record_audit(
            db, actor=current, action="auth.change_password_failed",
            resource_type="user", resource_id=current.id,
            after={"reason": "wrong_current_password"},
            request=request,
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "현재 비밀번호가 올바르지 않습니다.")

    try:
        validate_password(
            payload.new_password,
            username=current.username,
            current_hash=current.password_hash,
        )
    except PasswordPolicyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    current.password_hash = hash_password(payload.new_password)
    current.must_change_password = False
    # 비밀번호 변경 시 기존 세션 모두 무효화 (보안 best-practice).
    # session_version +1 → 옛 토큰들은 sv mismatch 로 401. 새 토큰은 갱신된
    # session_version 으로 발급되어 정상 동작.
    current.session_version = (current.session_version or 1) + 1
    db.commit()
    db.refresh(current)
    record_audit(
        db, actor=current, action="auth.change_password",
        resource_type="user", resource_id=current.id,
        after={"username": current.username},
        request=request,
    )
    token, exp = create_access_token(
        user_id=current.id, username=current.username, role=current.role.value,
        session_version=current.session_version,
    )
    return PasswordChangeResponse(
        user=UserOut.model_validate(current), access_token=token, expires_at=exp
    )