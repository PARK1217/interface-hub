from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

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