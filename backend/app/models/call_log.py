from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, func
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

    triggered_by: Mapped[str] = mapped_column(String(20), default="manual")  # manual|schedule
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    interface: Mapped["Interface"] = relationship(back_populates="call_logs", lazy="noload")  # noqa: F821