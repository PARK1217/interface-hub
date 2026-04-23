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
from app.core.time import now_kst
from app.models import User, UserRole
from app.schemas.user import (
    PasswordResetResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])


def _gen_temp_password(length: int = 12) -> str:
    """관리자가 비밀번호 초기화 시 사용. 사용자 첫 로그인 시 변경 권장."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
    if not payload.password or len(payload.password) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "비밀번호는 4자 이상")
    obj = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        email=payload.email,
        role=payload.role,
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


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> PasswordResetResponse:
    """관리자가 임시 비밀번호 발급. 응답에만 1회 노출. 사용자 첫 로그인 시 변경 권장."""
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    temp = _gen_temp_password(12)
    obj.password_hash = hash_password(temp)
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="user.reset_password",
        resource_type="user", resource_id=obj.id,
        after={"username": obj.username, "method": "admin_reset"},
        request=request,
    )
    return PasswordResetResponse(user_id=obj.id, username=obj.username, temp_password=temp)