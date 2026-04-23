from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    """변경 액션의 영구 감사 기록.

    Append-only 테이블 — UPDATE / DELETE 절대 안 함. 운영 환경에서는 PG
    트리거로 강제 차단 권장. 금감원 전산사고 보고 / 내부 감사 / 사고
    조사 시 "누가 언제 무엇을 어떤 값에서 어떤 값으로 바꿨는지" 답변
    가능해야 함.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 행위자: User 행이 비활성화/삭제돼도 actor_username 스냅샷으로 보존
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    actor_username: Mapped[str] = mapped_column(String(80), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(40), default=None)

    # 액션 코드 (예: "interface.create", "interface.execute", "auth.login_failed")
    action: Mapped[str] = mapped_column(String(80), index=True)
    # 영향받은 자원 (있을 경우)
    resource_type: Mapped[str | None] = mapped_column(String(40), default=None, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(40), default=None, index=True)

    # 변경 전/후 스냅샷 (JSON). UPDATE 액션이면 둘 다, CREATE 면 after, DELETE 면 before.
    before_value: Mapped[dict | None] = mapped_column(JSON, default=None)
    after_value: Mapped[dict | None] = mapped_column(JSON, default=None)

    ip: Mapped[str | None] = mapped_column(String(45), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )