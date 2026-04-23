from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, enum.Enum):
    """3단계 권한.

    - VIEWER: 모든 화면 read-only (감사관, 임원, 신입)
    - OPERATOR: 인터페이스 실행·재처리·장애 처리 (현장 운영자)
    - ADMIN: 인터페이스 CRUD·시크릿·사용자 관리 (시스템 관리자)
    """

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # bcrypt 해시. 평문 비밀번호는 절대 저장 X.
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(120), default=None)
    email: Mapped[str | None] = mapped_column(String(160), default=None)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.VIEWER, index=True)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 계정 비활성화 (soft disable). 행은 유지 — audit_logs FK 무결성 위해 hard delete 안 함.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())