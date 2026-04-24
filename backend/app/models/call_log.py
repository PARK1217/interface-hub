from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CallStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    AUTH_ERROR = "AUTH_ERROR"
    FORMAT_ERROR = "FORMAT_ERROR"
    SERVER_ERROR = "SERVER_ERROR"


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), index=True
    )

    request: Mapped[dict | None] = mapped_column(JSON, default=None)
    response: Mapped[dict | None] = mapped_column(JSON, default=None)

    status: Mapped[CallStatus] = mapped_column(Enum(CallStatus), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    error_type: Mapped[str | None] = mapped_column(String(80), default=None, index=True)
    error_trace: Mapped[str | None] = mapped_column(Text, default=None)

    triggered_by: Mapped[str] = mapped_column(String(20), default="manual")  # manual|schedule|reprocess
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # --- reprocessing lineage --------------------------------------------------
    parent_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_logs.id", ondelete="SET NULL"), default=None, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_reprocessed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Phase B.9 — 호출당 자동 재시도 시도 횟수 (1=재시도 없이 1회로 끝, 2=재시도 1회 후 성공/실패).
    # `retry_count` 와 별개 — 그건 "운영자 reprocess" 체인 깊이고, 이 컬럼은
    # "한 번의 execute 안에서 backoff 로 자동 재시도한 횟수". UI 에서 ↻×2 같이 표시.
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- actor (Phase A) -------------------------------------------------------
    # manual / reprocess / ingest 호출 시 어느 사용자가 트리거했는지.
    # schedule (cron) 호출은 NULL.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )

    interface: Mapped["Interface"] = relationship(back_populates="call_logs", lazy="noload")  # noqa: F821