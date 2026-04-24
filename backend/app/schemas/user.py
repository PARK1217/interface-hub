from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str | None = None
    email: str | None = None
    role: UserRole
    last_login_at: datetime | None = None
    disabled_at: datetime | None = None
    # / B.4 — 잠금 / 강제 비밀번호 변경 상태
    failed_login_count: int = 0
    locked_until: datetime | None = None
    must_change_password: bool = False
    created_at: datetime


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str | None = None
    email: str | None = None
    role: UserRole = UserRole.VIEWER


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    role: UserRole | None = None


class PasswordResetResponse(BaseModel):
    """관리자가 비밀번호 초기화 시 1회 표시되는 임시 비밀번호."""

    user_id: int
    username: str
    temp_password: str


class PasswordChangeRequest(BaseModel):
    """본인 비밀번호 변경."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class PasswordChangeResponse(BaseModel):
    """비밀번호 변경 후 새로 발급된 토큰을 함께 반환.

    비밀번호 변경 시 기존 세션 모두 무효화하므로 본 요청의
    토큰도 함께 무효가 됨. 프론트가 즉시 새 토큰으로 교체하도록 같이 응답.
    """

    user: UserOut
    access_token: str
    expires_at: datetime