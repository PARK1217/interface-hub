from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
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

    # --- Phase B.1 로그인 실패 lockout -------------------------------------
    # 연속 실패 횟수 (성공 시 0 으로 리셋). 임계치 (settings.lockout_threshold)
    # 도달 시 locked_until 에 해제 시각 기록 → 그 전까지 로그인 불가.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # --- Phase B.4 최초/관리자 발급 비밀번호 강제 변경 ---------------------
    # True 면 로그인은 되지만 다른 화면 진입 전 비밀번호 변경 강제.
    # 관리자가 user.create / reset_password 할 때 자동 True, 본인이
    # change-password 완료 시 False.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())