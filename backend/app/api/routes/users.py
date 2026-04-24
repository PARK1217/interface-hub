"""사용자 관리 — ADMIN 만 접근. 모든 변경 액션은 감사 로그에 기록.

비밀번호 평문은 어디에도 저장 안 됨. PasswordResetResponse 의 temp_password
는 1회 응답으로만 반환되고 audit_logs 에도 안 들어감 (해시만 비교).
"""

from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.core.auth import hash_password
from app.core.password_policy import PasswordPolicyError, validate_password
from app.models import User, UserRole
from app.schemas.user import (
    PasswordResetResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])


def _gen_temp_password(length: int = 14) -> str:
    """관리자 발급 임시 비밀번호 생성기.

    정책 (영문+숫자+특수문자) 을 반드시 만족하도록 각 그룹에서 최소 1자 보장 후
    나머지를 채우고 셔플. 사용자 첫 로그인 시 must_change_password 로 강제 변경.
    """
    letters = string.ascii_letters
    digits = string.digits
    specials = "!@#$%^&*-_=+"
    pool = letters + digits + specials
    if length < 4:
        length = 4
    chars = [
        secrets.choice(letters),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _=Depends(require_role([UserRole.ADMIN])),
) -> list[UserOut]:
    return [
        UserOut.model_validate(u)
        for u in db.scalars(select(User).order_by(User.id)).all()
    ]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    try:
        validate_password(payload.password, username=payload.username)
    except PasswordPolicyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    obj = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        email=payload.email,
        role=payload.role,
        # 관리자 발급 비밀번호는 첫 로그인 시 변경 강제 (Phase B.4)
        must_change_password=True,
    )
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "중복된 사용자명") from e
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="user.create",
        resource_type="user", resource_id=obj.id,
        after={"username": obj.username, "role": obj.role.value, "full_name": obj.full_name},
        request=request,
    )
    return UserOut.model_validate(obj)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(obj, k) for k in data}
    # role 변경은 감사 critical → 자기 자신의 role 변경 금지 (lockout 방지)
    if "role" in data and obj.id == actor.id and data["role"] != actor.role:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "자기 자신의 권한은 변경할 수 없습니다.")
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="user.update",
        resource_type="user", resource_id=obj.id,
        before={k: (v.value if hasattr(v, "value") else v) for k, v in before.items()},
        after={k: (v.value if hasattr(v, "value") else v) for k, v in data.items()},
        request=request,
    )
    return UserOut.model_validate(obj)


@router.post("/{user_id}/disable", response_model=UserOut)
def disable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if obj.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "자기 자신의 계정은 비활성화할 수 없습니다.")
    if obj.disabled_at is None:
        from app.core.time import now_kst
        obj.disabled_at = now_kst()
        db.commit()
        record_audit(
            db, actor=actor, action="user.disable",
            resource_type="user", resource_id=obj.id,
            after={"username": obj.username}, request=request,
        )
    db.refresh(obj)
    return UserOut.model_validate(obj)


@router.post("/{user_id}/enable", response_model=UserOut)
def enable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if obj.disabled_at is not None:
        obj.disabled_at = None
        db.commit()
        record_audit(
            db, actor=actor, action="user.enable",
            resource_type="user", resource_id=obj.id,
            after={"username": obj.username}, request=request,
        )
    db.refresh(obj)
    return UserOut.model_validate(obj)


@router.post("/{user_id}/force-logout", response_model=UserOut)
def force_logout_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    """대상 사용자의 모든 활성 세션 즉시 종료 (Phase B.6).

    session_version 을 +1 → 발급된 모든 JWT 가 sv mismatch 로 다음 요청부터
    401. 토큰 탈취 의심 / 퇴사 / 권한 회수 등 즉시 격리가 필요한 시나리오에 사용.
    """
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    obj.session_version = (obj.session_version or 1) + 1
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="user.force_logout",
        resource_type="user", resource_id=obj.id,
        after={"username": obj.username, "self_target": obj.id == actor.id},
        request=request,
    )
    return UserOut.model_validate(obj)


@router.post("/{user_id}/unlock", response_model=UserOut)
def unlock_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> UserOut:
    """잠긴 계정 강제 해제 (Phase B.1).

    failed_login_count 리셋 + locked_until 제거. 잠긴 상태가 아니어도 멱등.
    """
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    was_locked = bool(obj.locked_until) or (obj.failed_login_count or 0) > 0
    obj.failed_login_count = 0
    obj.locked_until = None
    db.commit()
    db.refresh(obj)
    if was_locked:
        record_audit(
            db, actor=actor, action="user.unlock",
            resource_type="user", resource_id=obj.id,
            after={"username": obj.username},
            request=request,
        )
    return UserOut.model_validate(obj)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> PasswordResetResponse:
    """관리자가 임시 비밀번호 발급. 응답에만 1회 노출.

    - must_change_password=True 로 강제 변경 유도 (Phase B.4)
    - 잠금/실패 카운트도 같이 초기화 (계정 복구 한 번에)
    """
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    temp = _gen_temp_password(14)
    obj.password_hash = hash_password(temp)
    obj.must_change_password = True
    obj.failed_login_count = 0
    obj.locked_until = None
    # 비밀번호가 바뀌었으니 기존 세션 모두 종료 (탈취 의심 시 reset 시나리오)
    obj.session_version = (obj.session_version or 1) + 1
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="user.reset_password",
        resource_type="user", resource_id=obj.id,
        after={"username": obj.username, "method": "admin_reset"},
        request=request,
    )
    return PasswordResetResponse(user_id=obj.id, username=obj.username, temp_password=temp)